#!/usr/bin/env python3
"""
CVD divergence walk-forward: конвертация тиков в рубли.
Берёт лучшие конфиги per symbol из wf_divergence_honest.py и пересчитывает в RUB.
"""
import clickhouse_connect
import pandas as pd
import numpy as np
import json

CH = clickhouse_connect.get_client(host='10.0.0.64', database='moex')

# Стоимость шага цены из moex.futures_info
TICK_COST = {}
try:
    info = CH.query_df("""
        SELECT asset_code, 
               round(tick_cost, 2) AS tick_cost_rub,
               min_step, contract_size
        FROM moex.futures_info 
        WHERE asset_code IN ('NG','BR','Si','MXI')
    """)
    for _, r in info.iterrows():
        TICK_COST[r['asset_code']] = r['tick_cost_rub'] if r['tick_cost_rub'] > 0 else 0.01
    print(f"Tick costs from DB: {TICK_COST}")
except:
    # Fallback
    TICK_COST = {'NG': 3.715, 'BR': 0.743, 'Si': 0.0025, 'MXI': 0.10}
    print(f"Tick costs fallback: {TICK_COST}")

TICK = {'NG': 0.0005, 'BR': 0.001, 'Si': 0.0025, 'MXI': 0.01}

BEST = {
    'NG':  {'lk': 10, 'hold': 3, 'q': 0.8},
    'BR':  {'lk': 20, 'hold': 1, 'q': 0.8},
    'Si':  {'lk': 10, 'hold': 5, 'q': 0.7},
    'MXI': {'lk': 20, 'hold': 1, 'q': 0.6},
}

all_results = {}

for SYM in ['NG', 'BR', 'Si', 'MXI']:
    print(f"\n{'='*60}", flush=True)
    print(f"  {SYM}", flush=True)
    print(f"{'='*60}", flush=True)
    
    tc = TICK_COST.get(SYM, 1.0)
    tick = TICK[SYM]
    lk = BEST[SYM]['lk']
    hold_bars = BEST[SYM]['hold']
    q = BEST[SYM]['q']
    
    print(f"  Loading...", flush=True)
    df = CH.query_df(f"""
        SELECT tradedate AS date,
               toDateTime(tradedate || ' ' || tradetime) AS time,
               pr_close AS close, vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{SYM}' AND vol > 0
        ORDER BY time
    """)
    df['time'] = pd.to_datetime(df['time'])
    df['cvd'] = df['vol_b'].fillna(0) - df['vol_s'].fillna(0)
    print(f"  Bars: {len(df):,}", flush=True)
    
    dates = sorted(df['date'].unique())
    
    all_trades = []
    i = 180
    while i < len(dates):
        test_end = min(i+60, len(dates))
        train_dates = set(dates[i-180:i])
        test_dates = set(dates[i:test_end])
        if len(test_dates) < 10:
            i += 60; continue
        
        train = df[df['date'].isin(train_dates)].copy()
        test = df[df['date'].isin(test_dates)].copy()
        if len(train) < 200 or len(test) < 20:
            i += 60; continue
        
        train['cvd_cum'] = train['cvd'].cumsum()
        train['price_chg'] = train['close'].diff(lk)
        train['cvd_cum_chg'] = train['cvd_cum'].diff(lk)
        train_valid = train.dropna()
        if len(train_valid) < 100:
            i += 60; continue
        
        p_thr = train_valid['price_chg'].abs().quantile(q)
        c_thr = train_valid['cvd_cum_chg'].abs().quantile(q)
        if p_thr == 0 or c_thr == 0:
            i += 60; continue
        
        last_cvd = train['cvd_cum'].iloc[-1]
        test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
        test['price_chg'] = test['close'].diff(lk)
        test['cvd_cum_chg'] = test['cvd_cum'].diff(lk)
        
        bearish = (test['price_chg'] > p_thr) & (test['cvd_cum_chg'] < -c_thr)
        bullish = (test['price_chg'] < -p_thr) & (test['cvd_cum_chg'] > c_thr)
        
        bearish_idx = set(test.index[bearish])
        bullish_idx = set(test.index[bullish])
        
        pos = 0; ep = 0.0; bars = 0
        for idx, row in test.iterrows():
            sig = 0
            if idx in bearish_idx: sig = -1
            elif idx in bullish_idx: sig = 1
            
            if sig == 0:
                if pos != 0:
                    bars += 1
                    if bars >= hold_bars:
                        pnl_ticks = round((row['close'] - ep) * pos / tick, 0)
                        pnl_rub = round(pnl_ticks * tc, 2)
                        all_trades.append({'time': str(row['time']), 'month': str(row['time'].to_period('M')),
                                           'pnl_ticks': int(pnl_ticks), 'pnl_rub': pnl_rub})
                        pos = 0
                continue
            
            if pos == 0:
                pos = sig; ep = row['close']; bars = 1
            else:
                bars += 1
                if bars >= hold_bars:
                    pnl_ticks = round((row['close'] - ep) * pos / tick, 0)
                    pnl_rub = round(pnl_ticks * tc, 2)
                    all_trades.append({'time': str(row['time']), 'month': str(row['time'].to_period('M')),
                                       'pnl_ticks': int(pnl_ticks), 'pnl_rub': pnl_rub})
                    pos = 0
        
        if pos != 0:
            pnl_ticks = round((test.iloc[-1]['close'] - ep) * pos / tick, 0)
            pnl_rub = round(pnl_ticks * tc, 2)
            all_trades.append({'time': str(test.iloc[-1]['time']), 'month': str(test.iloc[-1]['time'].to_period('M')),
                               'pnl_ticks': int(pnl_ticks), 'pnl_rub': pnl_rub})
        
        i += 60
    
    if not all_trades:
        print(f"  NO TRADES", flush=True)
        continue
    
    df_trades = pd.DataFrame(all_trades)
    total_ticks = df_trades['pnl_ticks'].sum()
    total_rub = df_trades['pnl_rub'].sum()
    wr = (df_trades['pnl_rub'] > 0).sum() / len(df_trades) * 100
    
    print(f"  Total trades: {len(df_trades):,}", flush=True)
    print(f"  Total ticks:  {total_ticks:+,.0f}", flush=True)
    print(f"  Total RUB:    {total_rub:+,.0f}", flush=True)
    print(f"  WR:           {wr:.1f}%", flush=True)
    
    # Месячная разбивка
    monthly = df_trades.groupby('month').agg(
        trades=('pnl_rub', 'count'),
        wins=('pnl_rub', lambda x: (x > 0).sum()),
        net_ticks=('pnl_ticks', 'sum'),
        net_rub=('pnl_rub', 'sum'),
    ).sort_index()
    monthly['wr'] = (monthly['wins'] / monthly['trades'] * 100).round(1)
    
    print(f"\n  {'Month':<10} {'Trades':>7} {'WR':>6} {'Net RUB':>12}", flush=True)
    print(f"  {'-'*36}", flush=True)
    for m, r in monthly.iterrows():
        print(f"  {m:<10} {r['trades']:>7.0f} {r['wr']:>5.1f}% {r['net_rub']:>+12,.0f}", flush=True)
    
    pos_months = (monthly['net_rub'] > 0).sum()
    print(f"  {'TOTAL':<10} {monthly['trades'].sum():>7.0f} "
          f"{(monthly['wins'].sum()/monthly['trades'].sum()*100):>5.1f}% "
          f"{monthly['net_rub'].sum():>+12,.0f}", flush=True)
    print(f"  Months+ (RUB): {pos_months}/{len(monthly)}", flush=True)
    
    all_results[SYM] = {
        'trades': len(df_trades),
        'wr': round(wr, 1),
        'total_ticks': int(total_ticks),
        'total_rub': round(total_rub, 2),
        'tick_cost': tc,
        'params': BEST[SYM],
        'pos_months': int(pos_months),
        'total_months': len(monthly),
    }

# Пул всех 4
all_dfs = []
for SYM in ['NG', 'BR', 'Si', 'MXI']:
    if SYM in all_results:
        all_dfs.append(1)

total_trades = sum(r['trades'] for r in all_results.values())
total_rub = sum(r['total_rub'] for r in all_results.values())
print(f"\n{'='*60}", flush=True)
print(f"  POOL ALL SYMBOLS", flush=True)
print(f"{'='*60}", flush=True)
print(f"  Total trades: {total_trades:,}", flush=True)
print(f"  Total RUB:    {total_rub:+,.0f}", flush=True)

all_results['_pool'] = {
    'trades': total_trades,
    'total_rub': round(total_rub, 2),
}

with open('reports/wf_divergence_rub.json', 'w') as f:
    json.dump(all_results, f, indent=2, default=str)

print(f"\n  Saved: reports/wf_divergence_rub.json", flush=True)
print(f"\nDone.", flush=True)
