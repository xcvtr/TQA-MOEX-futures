#!/usr/bin/env python3
"""
CVD divergence: поиск оптимального сдвига лимитки per-ticker.
Идея: лимитка выставляется на N тиков В СТОРОНУ СИГНАЛА,
чтобы повысить исполнение без существенного ухудшения цены.

- Сигнал bullish (+1): entry = close + N * tick (покупаем чуть выше рынка)
- Сигнал bearish (-1): entry = close - N * tick (продаём чуть ниже рынка)
"""
import clickhouse_connect
import pandas as pd
import numpy as np
import sys

ch = clickhouse_connect.get_client(host='10.0.0.64', database='moex')

INITIAL_CAPITAL = 100_000
SLIPPAGE = 0.5  # тиков на вход (базовый)

TICK_COST = {'NG': 3.715, 'BR': 0.743, 'Si': 0.0025, 'MXI': 0.10}
TICK = {'NG': 0.0005, 'BR': 0.001, 'Si': 0.0025, 'MXI': 0.01}
GO = {'NG': 4800, 'BR': 3500, 'Si': 2500, 'MXI': 2000}
SYMBOLS = ['NG', 'BR', 'Si', 'MXI']
N_SYMS = len(SYMBOLS)

# Боевой конфиг
TF = '5min'
LK = 20
HOLD = 1
Q = 0.6
WS_TRAIN = 180
WS_TEST = 60

# Сдвиги для проверки (тиков)
SHIFTS = [0, 0.5, 1, 1.5, 2, 3, 5]

def resample_to_tf(df, tf):
    df = df.copy()
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')
    ohlc = {'open': 'first', 'close': 'last', 'vol_b': 'sum', 'vol_s': 'sum'}
    resampled = df.resample(tf).agg(ohlc).dropna(subset=['open'])
    resampled = resampled.reset_index()
    resampled['cvd'] = resampled['vol_b'].fillna(0) - resampled['vol_s'].fillna(0)
    resampled['date'] = resampled['time'].dt.date
    return resampled

print("Loading 1m data...", flush=True)
raw_data = {}
for SYM in SYMBOLS:
    df = ch.query_df(f"""
        SELECT toDateTime(tradedate || ' ' || tradetime) AS time,
               pr_open AS open, pr_close AS close,
               vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{SYM}' AND vol > 0
        ORDER BY time
    """)
    raw_data[SYM] = df
    print(f"  {SYM}: {len(df):,} bars", flush=True)

data = {SYM: resample_to_tf(raw_data[SYM], TF) for SYM in SYMBOLS}

results = []

# 1. Per-ticker + portfolio grid
print(f"\n{'='*80}", flush=True)
print(f"  GRID: per-ticker сдвиг entry_price", flush=True)
print(f"  TF={TF} lk={LK} hold={HOLD} q={Q}", flush=True)
print(f"{'='*80}", flush=True)

for shift in SHIFTS:
    print(f"\n--- shift={shift} tick ---", flush=True)
    
    all_trades = []
    
    for SYM in SYMBOLS:
        df = data[SYM].copy()
        tc = TICK_COST[SYM]
        tick = TICK[SYM]
        
        dates = sorted(df['date'].unique())
        ws_train = min(WS_TRAIN, max(60, len(dates) // 3))
        ws_test = min(WS_TEST, max(20, len(dates) // 6))
        
        i = ws_train
        sym_trades = []
        
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
            train['pchg'] = train['close'].diff(LK).dropna()
            train['cchg'] = train['cvd_cum'].diff(LK).dropna()
            train_v = train.dropna()
            if len(train_v) < 30:
                i += ws_test; continue
            p_thr = train_v['pchg'].abs().quantile(Q)
            c_thr = train_v['cchg'].abs().quantile(Q)
            if p_thr == 0 or c_thr == 0:
                i += ws_test; continue
            
            last_cvd = train['cvd_cum'].iloc[-1]
            test['cvd_cum'] = last_cvd + test['cvd'].cumsum()
            test['pchg'] = test['close'].diff(LK)
            test['cchg'] = test['cvd_cum'].diff(LK)
            test_v = test.dropna()
            
            bearish = (test_v['pchg'] > p_thr) & (test_v['cchg'] < -c_thr)
            bullish = (test_v['pchg'] < -p_thr) & (test_v['cchg'] > c_thr)
            
            for sig_idx in range(len(test_v)):
                sig = -1 if bearish.iloc[sig_idx] else (1 if bullish.iloc[sig_idx] else 0)
                if sig == 0:
                    continue
                
                base_price = test_v.iloc[sig_idx]['close']
                # Сдвиг: в сторону сигнала
                entry_price = base_price + sig * shift * tick
                
                exit_idx = sig_idx + HOLD
                if exit_idx >= len(test_v):
                    exit_idx = len(test_v) - 1
                exit_price = test_v.iloc[exit_idx]['close']
                
                pnl_ticks = (exit_price - entry_price) * sig / tick
                slippage_cost = SLIPPAGE * tc
                pnl_rub = pnl_ticks * tc - slippage_cost
                
                sym_trades.append({
                    'time': test_v.iloc[exit_idx]['time'],
                    'pnl_rub': pnl_rub,
                    'symbol': SYM,
                    'sig': sig,
                })
            
            i += ws_test
        
        # Per-symbol stats
        if sym_trades:
            sym_df = pd.DataFrame(sym_trades)
            wr = (sym_df['pnl_rub'] > 0).mean() * 100
            net = sym_df['pnl_rub'].sum()
            avg = sym_df['pnl_rub'].mean()
            n = len(sym_trades)
            print(f"  {SYM}: n={n:5d} WR={wr:5.1f}% Net={net:+10.0f} Avg={avg:+8.0f}", flush=True)
        
        all_trades.extend(sym_trades)
    
    # Portfolio-level
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
    wr = (trades_df['pnl_rub'] > 0).mean() * 100
    
    mon_pnl = trades_df.groupby(trades_df['time'].dt.to_period('M')).agg(trades=('pnl_rub','count'), net=('pnl_rub','sum'))
    pos_m = (mon_pnl['net'] > 0).sum()
    
    duration_days = (trades_df['time'].max() - trades_df['time'].min()).days
    duration_years = max(duration_days / 365.25, 0.1)
    cagr = (capital / INITIAL_CAPITAL) ** (1 / duration_years) - 1 if capital > 0 else 0.0
    calmar = cagr / (max_dd / 100) if max_dd > 0.001 else 0.0
    
    print(f"  ───────────────────────────────────────────────────", flush=True)
    print(f"  PORTFOLIO shift={shift}tick: Trades={total_trades:,} Final={capital:,.0f} "
          f"Return={(capital/INITIAL_CAPITAL-1)*100:+6.1f}% WR={wr:5.1f}% "
          f"DD={max_dd:5.2f}% Calmar={calmar:.2f} M+={pos_m}/{len(mon_pnl)}", flush=True)
    
    results.append({
        'shift': shift,
        'trades': total_trades,
        'final_capital': round(capital, 2),
        'return_pct': round((capital/INITIAL_CAPITAL-1)*100, 1),
        'wr': round(wr, 1),
        'max_dd_pct': round(max_dd, 2),
        'calmar': round(calmar, 2),
        'pos_months': int(pos_m),
        'total_months': len(mon_pnl),
    })

print(f"\n{'='*80}", flush=True)
print(f"  SUMMARY (sorted by Calmar)", flush=True)
print(f"{'='*80}", flush=True)
print(f"{'Shift':>5} {'Trades':>7} {'Final':>12} {'Return%':>8} {'WR%':>5} {'DD%':>6} {'Calmar':>7} {'M+':>4}", flush=True)
print(f"{'-'*5} {'-'*7} {'-'*12} {'-'*8} {'-'*5} {'-'*6} {'-'*7} {'-'*4}", flush=True)
for r in sorted(results, key=lambda x: x['calmar'], reverse=True):
    print(f"{r['shift']:>4.1f}t {r['trades']:>7,} {r['final_capital']:>12,.0f} {r['return_pct']:>7.1f}% {r['wr']:>4.1f}% {r['max_dd_pct']:>5.2f}% {r['calmar']:>6.2f} {r['pos_months']:>2}/{r['total_months']:<2}", flush=True)

print("\nDone.", flush=True)
