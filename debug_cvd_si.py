#!/usr/bin/env python3
"""Debug CVD + Trailing TP on Si specifically."""
import sys, os
import numpy as np
import pandas as pd
import clickhouse_connect

ch = clickhouse_connect.get_client(host='10.0.0.60', port=8123, database='moex')

PERIOD = 20
Z_THRESH = 0.6
TRAIL_ACTIVATE = 0.005
TRAIL_TRAIL = 0.003
TIMEOUT_BARS = 96  # Use project's default: 96 bars
INITIAL_CAPITAL = 100_000.0
COMMISSION = 4.0
SLIPPAGE_TICKS = 1

# Si specs from PG
SI_SPEC = {
    'ticker': 'Si',
    'min_step': 1.0,
    'step_price': 1.0,  # 1 RUB per tick
    'lot': 1000,
    'go': 15252.32,
    'decimals': 0,
}

def load_bars(asset_code):
    q = f"""
        SELECT 
            toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
            argMax(pr_open, SYSTIME) as open,
            argMax(pr_high, SYSTIME) as high,
            argMax(pr_low, SYSTIME) as low,
            argMax(pr_close, SYSTIME) as close,
            sum(vol_b) as vol_b,
            sum(vol_s) as vol_s,
            count() as raw_rows
        FROM moex.tradestats_fo 
        WHERE asset_code = '{asset_code}' 
          AND SYSTIME >= '2024-10-01'
        GROUP BY bt 
        ORDER BY bt
    """
    df = ch.query_df(q)
    df['bt'] = pd.to_datetime(df['bt'])
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['vol_b'] = df['vol_b'].astype(float).fillna(0)
    df['vol_s'] = df['vol_s'].astype(float).fillna(0)
    return df

def compute_cvd_z(df):
    n = len(df)
    cvd = df['vol_b'].values - df['vol_s'].values
    dcvd = np.diff(cvd, prepend=cvd[0])
    dcvd_z = np.full(n, np.nan)
    for i in range(PERIOD, n):
        s = dcvd[i-PERIOD:i]
        if np.std(s) > 0:
            dcvd_z[i] = (dcvd[i] - np.mean(s)) / np.std(s)
    df['cvd'] = cvd
    df['dcvd'] = dcvd
    df['dcvd_z'] = dcvd_z
    return df

print("Loading Si data...")
df = load_bars('Si')
print(f"Loaded {len(df)} bars")

df = compute_cvd_z(df)
n = len(df)

spec = SI_SPEC
min_step = spec['min_step']
step_price = spec['step_price']
lot = spec['lot']
go = spec['go']

# Signal detection
long_signals = df['dcvd_z'].values > Z_THRESH
short_signals = df['dcvd_z'].values < -Z_THRESH

print(f"Long signals: {np.sum(long_signals)}")
print(f"Short signals: {np.sum(short_signals)}")

# Run backtest
equity = float(INITIAL_CAPITAL)
trades = []

for i in range(PERIOD, n - 1):
    signal = None
    if long_signals[i]:
        signal = 'long'
    elif short_signals[i]:
        signal = 'short'
    
    if signal is None:
        continue
    
    # Entry on next bar open + slippage
    entry_price = df['open'].iloc[i + 1]
    slippage = min_step * SLIPPAGE_TICKS
    if signal == 'long':
        entry_price += slippage
    else:
        entry_price -= slippage
    
    # Position: floor(equity * 0.1 / go), min 1
    contracts = max(1, int(equity * 0.1 / go))
    
    # Exit management
    direction = 1 if signal == 'long' else -1
    trail_active = False
    trail_extreme = None
    
    for j in range(i + 1, min(n, i + 1 + TIMEOUT_BARS)):
        bar_idx = j
        current_price = df['close'].iloc[bar_idx]
        
        # Check trailing
        if signal == 'long':
            if not trail_active:
                ret = (current_price - entry_price) / entry_price
                if ret >= TRAIL_ACTIVATE:
                    trail_active = True
                    trail_extreme = current_price
                    # print(f"  TRAIL ACTIVATED at {current_price}")
            else:
                if current_price > trail_extreme:
                    trail_extreme = current_price
                trail_ret = (trail_extreme - current_price) / entry_price
                if trail_ret >= TRAIL_TRAIL:
                    # Exit on trail
                    exit_price = current_price
                    exit_reason = 'trail'
                    bars_held = bar_idx - i
                    
                    price_diff = (exit_price - entry_price) * direction
                    pnl_ticks = price_diff / min_step
                    gross_pnl = pnl_ticks * step_price * contracts * lot
                    comm = COMMISSION * 2 * contracts
                    net_pnl = gross_pnl - comm
                    equity += net_pnl
                    
                    trades.append({
                        'entry_time': df['bt'].iloc[i],
                        'exit_time': df['bt'].iloc[bar_idx],
                        'direction': signal,
                        'entry': entry_price,
                        'exit': exit_price,
                        'contracts': contracts,
                        'pnl_rub': net_pnl,
                        'gross_pnl': gross_pnl,
                        'commission': comm,
                        'reason': exit_reason,
                        'bars_held': bars_held,
                    })
                    break
        else:  # short
            if not trail_active:
                ret = (entry_price - current_price) / entry_price
                if ret >= TRAIL_ACTIVATE:
                    trail_active = True
                    trail_extreme = current_price
            else:
                if current_price < trail_extreme:
                    trail_extreme = current_price
                trail_ret = (current_price - trail_extreme) / entry_price
                if trail_ret >= TRAIL_TRAIL:
                    exit_price = current_price
                    exit_reason = 'trail'
                    bars_held = bar_idx - i
                    
                    price_diff = (exit_price - entry_price) * direction
                    pnl_ticks = price_diff / min_step
                    gross_pnl = pnl_ticks * step_price * contracts * lot
                    comm = COMMISSION * 2 * contracts
                    net_pnl = gross_pnl - comm
                    equity += net_pnl
                    
                    trades.append({
                        'entry_time': df['bt'].iloc[i],
                        'exit_time': df['bt'].iloc[bar_idx],
                        'direction': signal,
                        'entry': entry_price,
                        'exit': exit_price,
                        'contracts': contracts,
                        'pnl_rub': net_pnl,
                        'gross_pnl': gross_pnl,
                        'commission': comm,
                        'reason': exit_reason,
                        'bars_held': bars_held,
                    })
                    break
    else:
        # Timeout exit
        bar_idx = min(n - 1, i + TIMEOUT_BARS)
        exit_price = df['close'].iloc[bar_idx]
        exit_reason = 'timeout'
        bars_held = bar_idx - i
        
        price_diff = (exit_price - entry_price) * direction
        pnl_ticks = price_diff / min_step
        gross_pnl = pnl_ticks * step_price * contracts * lot
        comm = COMMISSION * 2 * contracts
        net_pnl = gross_pnl - comm
        equity += net_pnl
        
        trades.append({
            'entry_time': df['bt'].iloc[i],
            'exit_time': df['bt'].iloc[bar_idx],
            'direction': signal,
            'entry': entry_price,
            'exit': exit_price,
            'contracts': contracts,
            'pnl_rub': net_pnl,
            'gross_pnl': gross_pnl,
            'commission': comm,
            'reason': exit_reason,
            'bars_held': bars_held,
        })

print(f"\nTotal trades: {len(trades)}")
if trades:
    pnls = np.array([t['pnl_rub'] for t in trades])
    print(f"Total PnL: {np.sum(pnls):.2f} RUB")
    print(f"Mean PnL: {np.mean(pnls):.2f} RUB")
    print(f"Win rate: {np.mean(pnls > 0) * 100:.1f}%")
    print(f"Max win: {np.max(pnls):.2f}")
    print(f"Max loss: {np.min(pnls):.2f}")
    
    # Show first 10 trades
    print("\nFirst 10 trades:")
    for t in trades[:10]:
        print(f"  {t['direction']:5s} entry={t['entry']:>10.1f} exit={t['exit']:>10.1f} "
              f"contracts={t['contracts']:>3d} pnl={t['pnl_rub']:>+10.2f} "
              f"({t['reason']:7s}) bars={t['bars_held']}")
    
    # Show reason distribution
    reasons = {}
    for t in trades:
        r = t['reason']
        reasons[r] = reasons.get(r, 0) + 1
    print(f"\nExit reasons: {reasons}")
else:
    print("NO TRADES!")
    
    # Check signal distribution
    valid = ~np.isnan(df['dcvd_z'].values)
    print(f"Valid z-scores: {np.sum(valid)}/{n}")
    if np.sum(valid) > 0:
        z_vals = df['dcvd_z'].values[valid]
        print(f"Z-score range: [{np.min(z_vals):.2f}, {np.max(z_vals):.2f}]")
        print(f"Z-score mean: {np.mean(z_vals):.2f}, std: {np.std(z_vals):.2f}")
        print(f"Z > 0.6: {np.sum(z_vals > 0.6)} ({np.mean(z_vals > 0.6)*100:.1f}%)")
        print(f"Z < -0.6: {np.sum(z_vals < -0.6)} ({np.mean(z_vals < -0.6)*100:.1f}%)")
