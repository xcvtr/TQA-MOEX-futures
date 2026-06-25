#!/usr/bin/env python3
"""
CVD divergence: ПОРТФЕЛЬНЫЙ walk-forward v4.
- Вход ЛИМИТКОЙ по цене сигнала (close бара N)
- Выход по close через hold баров
- Комиссия: мейкерская = 0₽ (лимитки)
- Slippage: 0.5 тика на вход (риск неисполнения)
- Trade-level DD, margin locking
"""
import clickhouse_connect
import pandas as pd
import numpy as np
import json, sys
from itertools import product

ch = clickhouse_connect.get_client(host='10.0.0.64', database='moex')

INITIAL_CAPITAL = 100_000
COMMISSION = 0.0  # мейкер — лимитки
SLIPPAGE = 0.5    # slippage в тиках на вход (риск неполного исполнения)

TICK_COST = {'NG': 3.715, 'BR': 0.743, 'Si': 0.0025, 'MXI': 0.10}
TICK = {'NG': 0.0005, 'BR': 0.001, 'Si': 0.0025, 'MXI': 0.01}
GO = {'NG': 4800, 'BR': 3500, 'Si': 2500, 'MXI': 2000}
SYMBOLS = ['NG', 'BR', 'Si', 'MXI']
N_SYMS = len(SYMBOLS)

TIMEFRAMES = ['5min', '15min', '1h']
LOOKBACKS = [5, 10, 20]
HOLDS = [1, 2, 3, 5]
QS = [0.6, 0.7, 0.8]

def resample_to_tf(df, tf):
    df = df.copy()
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')
    rule_map = {'5min': '5min', '15min': '15min', '1h': '1h'}
    ohlc = {'open': 'first', 'close': 'last', 'vol_b': 'sum', 'vol_s': 'sum'}
    resampled = df.resample(rule_map[tf]).agg(ohlc).dropna(subset=['open'])
    resampled = resampled.reset_index()
    resampled['cvd'] = resampled['vol_b'].fillna(0) - resampled['vol_s'].fillna(0)
    resampled['date'] = resampled['time'].dt.date
    return resampled

# Load 1m
raw_data = {}
for SYM in SYMBOLS:
    sys.stdout.write(f"Loading {SYM}...\n"); sys.stdout.flush()
    df = ch.query_df(f"""
        SELECT toDateTime(tradedate || ' ' || tradetime) AS time,
               pr_open AS open, pr_close AS close,
               vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{SYM}' AND vol > 0
        ORDER BY time
    """)
    raw_data[SYM] = df

results = []

for tf in TIMEFRAMES:
    sys.stdout.write(f"\n{'='*70}\n  TIMEFRAME: {tf} (лимитка, комиссия=0)\n{'='*70}\n"); sys.stdout.flush()
    
    data = {SYM: resample_to_tf(raw_data[SYM], tf) for SYM in SYMBOLS}
    for SYM in SYMBOLS:
        sys.stdout.write(f"  {SYM}: {len(data[SYM])} bars\n"); sys.stdout.flush()
    
    for lk, hold_bars, q in product(LOOKBACKS, HOLDS, QS):
        sys.stdout.write(f"\n--- {tf} lk={lk} hold={hold_bars} q={q} ---\n"); sys.stdout.flush()
        
        all_trades = []
        
        for SYM in SYMBOLS:
            df = data[SYM].copy()
            tc = TICK_COST[SYM]
            tick = TICK[SYM]
            
            dates = sorted(df['date'].unique())
            ws_train = min(180, max(60, len(dates) // 3))
            ws_test = min(60, max(20, len(dates) // 6))
            
            i = ws_train
            while i < len(dates):
                test_end = min(i + ws_test, len(dates))
                train_dates = set(dates[i-ws_train:i])
                test_dates = set(dates[i:test_end])
                if len(test_dates) < 20:
                    i += ws_test; continue
                
                train = df[df['date'].isin(train_dates)].copy()
                test = df[df['date'].isin(test_dates)].copy().reset_index(drop=True)
                if len(train) < 50 or len(test) < 10:
                    i += ws_test; continue
                
                train['cvd_cum'] = train['cvd'].cumsum()
                train['pchg'] = train['close'].diff(lk).dropna()
                train['cchg'] = train['cvd_cum'].diff(lk).dropna()
                train_v = train.dropna()
                if len(train_v) < 30:
                    i += ws_test; continue
                p_thr = train_v['pchg'].abs().quantile(q)
                c_thr = train_v['cchg'].abs().quantile(q)
                if p_thr == 0 or c_thr == 0:
                    i += ws_test; continue
                
                last_cvd = train['cvd_cum'].iloc[-1]
                test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
                test['pchg'] = test['close'].diff(lk)
                test['cchg'] = test['cvd_cum'].diff(lk)
                test_v = test.dropna()
                
                bearish = (test_v['pchg'] > p_thr) & (test_v['cchg'] < -c_thr)
                bullish = (test_v['pchg'] < -p_thr) & (test_v['cchg'] > c_thr)
                
                # Вход лимиткой по цене сигнала (close бара N)
                # Выставляем на баре N+1 по цене close бара N
                # Исполнение гарантировано (лимитка по рынку), но со slippage
                for sig_idx in range(len(test_v)):
                    sig = -1 if bearish.iloc[sig_idx] else (1 if bullish.iloc[sig_idx] else 0)
                    if sig == 0:
                        continue
                    
                    # Цена входа = close сигнального бара (лимитка)
                    entry_price = test_v.iloc[sig_idx]['close']
                    
                    exit_idx = sig_idx + hold_bars
                    if exit_idx >= len(test_v):
                        exit_idx = len(test_v) - 1
                    
                    exit_price = test_v.iloc[exit_idx]['close']
                    
                    pnl_ticks = (exit_price - entry_price) * sig / tick
                    # slippage только на вход (лимитка может не исполниться полностью)
                    slippage_cost = SLIPPAGE * tc
                    # комиссия = 0 (мейкер)
                    pnl_rub = pnl_ticks * tc - slippage_cost
                    
                    all_trades.append({
                        'time': test_v.iloc[exit_idx]['time'],
                        'pnl_rub': pnl_rub,
                        'symbol': SYM,
                        'month': str(test_v.iloc[exit_idx]['time'].to_period('M')),
                        'sig': sig,
                    })
                
                i += ws_test
        
        if not all_trades:
            continue
        
        trades_df = pd.DataFrame(all_trades).sort_values('time')
        
        capital = INITIAL_CAPITAL
        peak = capital
        max_dd = 0.0
        
        for _, trade in trades_df.iterrows():
            go_entry = GO.get(trade['symbol'], 3000)
            max_lots = max(1, int(capital / N_SYMS / go_entry))
            lots = min(4, max_lots)
            capital += trade['pnl_rub'] * lots
            peak = max(peak, capital)
            dd = (peak - capital) / peak * 100
            max_dd = max(max_dd, dd)
        
        total_trades = len(all_trades)
        
        mon_pnl = trades_df.groupby('month').agg(trades=('pnl_rub','count'), net=('pnl_rub','sum'))
        pos_m = (mon_pnl['net'] > 0).sum()
        
        duration_days = (trades_df['time'].max() - trades_df['time'].min()).days
        duration_years = max(duration_days / 365.25, 0.1)
        
        if capital <= 0:
            cagr = 0.0
        else:
            cagr = (capital / INITIAL_CAPITAL) ** (1 / duration_years) - 1
        
        calmar = cagr / (max_dd / 100) if max_dd > 0.001 else 0.0
        wr = (trades_df['pnl_rub'] > 0).sum() / len(trades_df) * 100
        
        sys.stdout.write(f"  Trades: {total_trades:,}\n")
        sys.stdout.write(f"  Final: {capital:,.0f} RUB\n")
        sys.stdout.write(f"  Return: {(capital/INITIAL_CAPITAL-1)*100:+.1f}%\n")
        sys.stdout.write(f"  CAGR: {cagr*100:+.1f}%\n")
        sys.stdout.write(f"  WR: {wr:.1f}%\n")
        sys.stdout.write(f"  Max DD: {max_dd:.2f}%\n")
        sys.stdout.write(f"  Calmar: {calmar:.2f}\n")
        sys.stdout.write(f"  Months+: {pos_m}/{len(mon_pnl)}\n")
        
        results.append({
            'tf': tf, 'lk': lk, 'hold': hold_bars, 'q': q,
            'trades': total_trades,
            'final_capital': round(capital, 2),
            'return_pct': round((capital/INITIAL_CAPITAL-1)*100, 1),
            'cagr_pct': round(cagr*100, 1),
            'wr': round(wr, 1),
            'max_dd_pct': round(max_dd, 2),
            'calmar': round(calmar, 2),
            'pos_months': int(pos_m),
            'total_months': len(mon_pnl),
        })

sys.stdout.write(f"\n{'='*80}\n  ALL RESULTS v4 (LIMIT ORDER, sorted by Calmar)\n{'='*80}\n")
results.sort(key=lambda x: x['calmar'], reverse=True)

sys.stdout.write(f"{'TF':>4} {'lk':>3} {'hold':>4} {'q':>4} {'Trades':>7} {'Final(RUB)':>12} {'Return%':>8} {'CAGR%':>7} {'WR%':>5} {'DD%':>6} {'Calmar':>7} {'M+':>4}\n")
sys.stdout.write(f"{'-'*4} {'-'*3} {'-'*4} {'-'*4} {'-'*7} {'-'*12} {'-'*8} {'-'*7} {'-'*5} {'-'*6} {'-'*7} {'-'*4}\n")
for r in results[:25]:
    sys.stdout.write(f"{r['tf']:>4} {r['lk']:>3} {r['hold']:>4} {r['q']:>4.1f} {r['trades']:>7,} {r['final_capital']:>12,.0f} {r['return_pct']:>7.1f}% {r['cagr_pct']:>6.1f}% {r['wr']:>4.1f}% {r['max_dd_pct']:>5.2f}% {r['calmar']:>6.2f} {r['pos_months']:>2}/{r['total_months']:<2}\n")

best = results[0] if results else None
if best:
    sys.stdout.write(f"\nBEST: {best['tf']} lk={best['lk']} hold={best['hold']} q={best['q']}\n")

with open('reports/wf_divergence_grid_v4.json', 'w') as f:
    json.dump({'results': results, 'best': best}, f, indent=2)
sys.stdout.write(f"\nSaved: reports/wf_divergence_grid_v4.json\nDone.\n")
