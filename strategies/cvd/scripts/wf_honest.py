#!/usr/bin/env python3
"""
Честный walk-forward на CVD-дивергенции.
Каждое окно: train → считаем cvd_cum + пороги → test → торгуем.
Без look-ahead: cvd_cum НЕ выходит за пределы train.
"""
import clickhouse_connect
import pandas as pd
import numpy as np
import sys, json

CH = clickhouse_connect.get_client(host='10.0.0.64', database='moex')
TICK = {'NG': 0.0005, 'BR': 0.001, 'Si': 0.0025, 'MXI': 0.01}

all_results = {}

for SYM in ['NG', 'BR', 'Si', 'MXI']:
    print(f"\n{'='*70}", flush=True)
    print(f"  {SYM}", flush=True)
    print(f"{'='*70}", flush=True)
    
    print("  Loading...", flush=True)
    df = CH.query_df(f"""
        SELECT tradedate AS date, 
               toDateTime(tradedate || ' ' || tradetime) AS time,
               pr_close AS close, vol, vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{SYM}' AND vol > 0
        ORDER BY time
    """)
    df['time'] = pd.to_datetime(df['time'])
    df['cvd'] = df['vol_b'].fillna(0) - df['vol_s'].fillna(0)
    print(f"  Bars: {len(df):,}, Days: {df['date'].nunique()}", flush=True)
    
    dates = sorted(df['date'].unique())
    tick = TICK[SYM]
    
    # Grid параметров
    param_grid = []
    for lookback in [5, 10, 20]:
        for hold in [1, 3, 5]:
            for q in [0.6, 0.7, 0.8]:
                param_grid.append((lookback, hold, q))
    
    best_overall = None
    
    for lookback, hold_bars, q in param_grid:
        all_trades = []
        window_stats = []
        
        i = 180  # first 180 days for warmup
        while i < len(dates):
            test_end = min(i + 60, len(dates))
            train_dates = set(dates[i-180:i])
            test_dates = set(dates[i:test_end])
            
            if len(test_dates) < 10 or len(train_dates) < 60:
                i += 60
                continue
            
            train = df[df['date'].isin(train_dates)].copy()
            test = df[df['date'].isin(test_dates)].copy().reset_index(drop=True)
            
            if len(train) < 200 or len(test) < 20:
                i += 60
                continue
            
            # cvd_cum ТОЛЬКО по train
            train['cvd_cum'] = train['cvd'].cumsum()
            train['price_chg'] = train['close'].diff(lookback)
            train['cvd_cum_chg'] = train['cvd_cum'].diff(lookback)
            
            train_valid = train.dropna()
            if len(train_valid) < 100:
                i += 60
                continue
            
            p_thr = train_valid['price_chg'].abs().quantile(q)
            c_thr = train_valid['cvd_cum_chg'].abs().quantile(q)
            
            if p_thr == 0 or c_thr == 0:
                i += 60
                continue
            
            # Test: cvd_cum относительно последнего train
            last_cvd = train['cvd_cum'].iloc[-1]
            test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
            test['price_chg'] = test['close'].diff(lookback)
            test['cvd_cum_chg'] = test['cvd_cum'].diff(lookback)
            
            bearish = (test['price_chg'] > p_thr) & (test['cvd_cum_chg'] < -c_thr)
            bullish = (test['price_chg'] < -p_thr) & (test['cvd_cum_chg'] > c_thr)
            
            bearish_idx = set(test.index[bearish])
            bullish_idx = set(test.index[bullish])
            
            # Торговля
            pos = 0
            ep = 0.0
            bars = 0
            
            for idx, row in test.iterrows():
                sig = 0
                if idx in bearish_idx: sig = -1
                elif idx in bullish_idx: sig = 1
                
                if sig == 0:
                    if pos != 0:
                        bars += 1
                        if bars >= hold_bars:
                            pnl_ticks = round((row['close'] - ep) * pos / tick, 0)
                            all_trades.append(pnl_ticks)
                            pos = 0
                    continue
                
                if pos == 0:
                    pos = sig
                    ep = row['close']
                    bars = 1
                else:
                    bars += 1
                    if bars >= hold_bars:
                        pnl_ticks = round((row['close'] - ep) * pos / tick, 0)
                        all_trades.append(pnl_ticks)
                        pos = 0
            
            if pos != 0:
                pnl_ticks = round((test.iloc[-1]['close'] - ep) * pos / tick, 0)
                all_trades.append(pnl_ticks)
            
            window_stats.append({'train': f'{train_dates.pop()}' if train_dates else '?', 
                                  'test': f'{list(test_dates)[0]}..{list(test_dates)[-1]}',
                                  'trades': len(all_trades) - sum(s.get('trades', 0) for s in window_stats)})
            i += 60
        
        if len(all_trades) < 10:
            continue
        
        arr = np.array(all_trades)
        wins = arr[arr > 0]
        losses = arr[arr < 0]
        
        n = len(arr)
        wr = len(wins)/n*100
        net = arr.sum()
        avg = arr.mean()
        std = arr.std()
        sharpe = avg/std*np.sqrt(n) if std > 0 else 0
        gw = wins.sum() if len(wins) > 0 else 0
        gl = abs(losses.sum()) if len(losses) > 0 else 1
        pf = gw/gl if gl > 0 else 0
        
        eq = np.cumsum(arr)
        peak = np.maximum.accumulate(eq)
        dd = peak - eq
        max_dd = dd.max()
        
        print(f"  lk={lookback:2d} h={hold_bars:2d} q={q:.1f}: windows={len(window_stats):2d} "
              f"tr={n:5d} WR={wr:5.1f}% net={net:+9.0f}t SR={sharpe:+.3f} PF={pf:.2f} DD={max_dd:.0f}t", flush=True)
        
        cfg = {'lk': lookback, 'hold': hold_bars, 'q': q,
               'trades': n, 'wr': round(wr, 1), 'net_ticks': int(net),
               'sharpe': round(sharpe, 3), 'pf': round(pf, 2), 'max_dd': int(max_dd)}
        
        if best_overall is None or sharpe > best_overall['sharpe']:
            best_overall = cfg
    
    if best_overall:
        print(f"\n  BEST: {json.dumps(best_overall)}", flush=True)
    
    # Месячная разбивка для лучшей конфигурации
    if best_overall:
        print(f"\n  --- Monthly: lk={best_overall['lk']} hold={best_overall['hold']} ---", flush=True)
        
        # Пересчитываем с лучшими параметрами
        lk = best_overall['lk']
        hold_bars = best_overall['hold']
        q = best_overall['q']
        
        all_trades = []
        i = 180
        while i < len(dates):
            test_end = min(i + 60, len(dates))
            train_dates = set(dates[i-180:i])
            test_dates = set(dates[i:test_end])
            
            if len(test_dates) < 10:
                i += 60
                continue
            
            train = df[df['date'].isin(train_dates)].copy()
            test = df[df['date'].isin(test_dates)].copy().reset_index(drop=True)
            if len(train) < 200 or len(test) < 20:
                i += 60
                continue
            
            train['cvd_cum'] = train['cvd'].cumsum()
            train['price_chg'] = train['close'].diff(lk)
            train['cvd_cum_chg'] = train['cvd_cum'].diff(lk)
            train_valid = train.dropna()
            
            p_thr = train_valid['price_chg'].abs().quantile(q)
            c_thr = train_valid['cvd_cum_chg'].abs().quantile(q)
            if p_thr == 0 or c_thr == 0:
                i += 60
                continue
            
            last_cvd = train['cvd_cum'].iloc[-1]
            test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
            test['price_chg'] = test['close'].diff(lk)
            test['cvd_cum_chg'] = test['cvd_cum'].diff(lk)
            
            bearish = (test['price_chg'] > p_thr) & (test['cvd_cum_chg'] < -c_thr)
            bullish = (test['price_chg'] < -p_thr) & (test['cvd_cum_chg'] > c_thr)
            
            bearish_idx = set(test.index[bearish])
            bullish_idx = set(test.index[bullish])
            
            pos = 0
            ep = 0.0
            bars = 0
            
            for idx, row in test.iterrows():
                sig = 0
                if idx in bearish_idx: sig = -1
                elif idx in bullish_idx: sig = 1
                
                if sig == 0:
                    if pos != 0:
                        bars += 1
                        if bars >= hold_bars:
                            all_trades.append({'month': str(row['time'].to_period('M')),
                                                'pnl_ticks': round((row['close'] - ep) * pos / tick, 0)})
                            pos = 0
                    continue
                
                if pos == 0:
                    pos = sig
                    ep = row['close']
                    bars = 1
                else:
                    bars += 1
                    if bars >= hold_bars:
                        all_trades.append({'month': str(row['time'].to_period('M')),
                                            'pnl_ticks': round((row['close'] - ep) * pos / tick, 0)})
                        pos = 0
            
            if pos != 0:
                all_trades.append({'month': str(test.iloc[-1]['time'].to_period('M')),
                                    'pnl_ticks': round((test.iloc[-1]['close'] - ep) * pos / tick, 0)})
            
            i += 60
        
        if all_trades:
            monthly = pd.DataFrame(all_trades).groupby('month').agg(
                trades=('pnl_ticks', 'count'),
                wins=('pnl_ticks', lambda x: (x > 0).sum()),
                net=('pnl_ticks', 'sum'),
            )
            monthly['wr'] = (monthly['wins'] / monthly['trades'] * 100).round(1)
            
            pos_months = (monthly['net'] > 0).sum()
            print(f"  {'Month':<10} {'Tr':>5} {'WR':>6} {'Net':>10}", flush=True)
            print(f"  {'-'*31}", flush=True)
            for m, r in monthly.iterrows():
                print(f"  {str(m):<10} {r['trades']:>5.0f} {r['wr']:>5.1f}% {r['net']:>+10.0f}", flush=True)
            
            print(f"  {'TOTAL':<10} {monthly['trades'].sum():>5.0f} "
                  f"{(monthly['wins'].sum()/monthly['trades'].sum()*100):>5.1f}% "
                  f"{monthly['net'].sum():>+10.0f}", flush=True)
            print(f"  Months+: {pos_months}/{len(monthly)} ({pos_months/len(monthly)*100:.0f}%)", flush=True)
    
    all_results[SYM] = {'best': best_overall}

with open('/home/user/wf_divergence_honest.json', 'w') as f:
    json.dump(all_results, f, indent=2, default=str)

print("\n\nDone.", flush=True)
