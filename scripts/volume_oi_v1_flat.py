#!/usr/bin/env python3
"""
Volume × OI — Вариант 1: Flat sizing без реинвеста.
1 контракт на сделку, 200K стартовый капитал, PnL накапливается отдельно.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# ── Config ──
TICKERS = ['NR', 'CC', 'IB', 'GD', 'SR', 'PD']
DAYS = 400
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')
CAPITAL = 200000.0
COMMISSION = 2  # RUB/contract round-trip

PARAMS = {
    'NR': {'vol_z': 3.0, 'yur_z': 1.5, 'horizon': 6,  'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'vol_z': 3.5, 'yur_z': 1.5, 'horizon': 3,  'sl_pct': 0.01, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'IB': {'vol_z': 3.5, 'yur_z': 2.0, 'horizon': 12, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GD': {'vol_z': 3.5, 'yur_z': 1.5, 'horizon': 12, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SR': {'vol_z': 3.5, 'yur_z': 1.5, 'horizon': 12, 'sl_pct': 0.02, 'go': 5719, 'minstep': 0.01, 'tick_rub': 1.0},
    'PD': {'vol_z': 3.0, 'yur_z': 1.5, 'horizon': 3,  'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}

def rolling_zs(vals, w=20):
    s = pd.Series(vals).ffill()
    mu = s.rolling(w, min_periods=w//2).mean()
    sd = s.rolling(w, min_periods=w//2).std().replace(0, 1)
    return ((s - mu) / sd).fillna(0)

def calc_pnl_rub(direction, entry, exit_price, contracts, cfg):
    moves = (exit_price - entry) / cfg['minstep']
    if direction.upper() == 'SHORT':
        moves = -moves
    return moves * cfg['tick_rub'] * contracts

def max_dd_from_equity(equity):
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        mdd = max(mdd, dd)
    return mdd

# ── 1. Load data ──────────────────────────────────────────────
print("[1] Loading data...")
all_ohlcv = {}
all_signals = []

for ticker in TICKERS:
    print(f"  {ticker}...", end=' ', flush=True)
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m AS p
        INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0
        ORDER BY p.time
    """, parameters={'t': ticker, 's': since}).result_rows

    if not rows or len(rows) < 200:
        print(f"SKIP: only {len(rows) if rows else 0} bars")
        continue

    df = pd.DataFrame(rows, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi'
    ])
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['vol_z'] = rolling_zs(df['volume'], 20)
    df['fiz_z'] = rolling_zs(df['fiz_net'], 20)
    df['yur_z'] = rolling_zs(df['yur_net'], 20)
    all_ohlcv[ticker] = df

    p = PARAMS[ticker]
    mask = (df['vol_z'] > p['vol_z']) & (df['yur_z'] > p['yur_z']) & (df['fiz_z'] < 0)
    sig_idx = df[mask].index.tolist()
    sigs = []
    for idx in sig_idx:
        entry_idx = idx + 1
        if entry_idx >= len(df):
            continue
        entry_open = float(df.iloc[entry_idx]['open'])
        if entry_open <= 0:
            continue
        sigs.append({
            'ticker': ticker,
            'time': df.iloc[entry_idx]['time'],
            'entry_open': entry_open,
            'entry_idx': entry_idx,
            'horizon': p['horizon'],
            'sl_pct': p['sl_pct'],
            'vol_z': float(df.iloc[idx]['vol_z']),
            'yur_z': float(df.iloc[idx]['yur_z']),
        })
    all_signals.extend(sigs)
    print(f"{len(sigs)} signals / {len(df):,} bars")

all_signals.sort(key=lambda s: s['time'])
print(f"  Total signals across all tickers: {len(all_signals)}")

# ── 2. Simulate each signal ───────────────────────────────────
print("\n[2] Simulating trades (flat sizing, 1 contract)...")

trades = []
for sig in all_signals:
    tk = sig['ticker']
    cfg = PARAMS[tk]
    df = all_ohlcv[tk]
    i = sig['entry_idx']
    entry = sig['entry_open']
    stop_price = entry * (1 - cfg['sl_pct'])
    horizon = cfg['horizon']
    exit_idx = i + horizon
    if exit_idx >= len(df):
        continue

    exit_price = None
    exit_reason = None

    # Check each bar from i+1 (first bar after entry) to exit_idx
    for j in range(i + 1, exit_idx + 1):
        low_j = float(df.iloc[j]['low'])
        if low_j <= stop_price:
            exit_price = low_j
            exit_reason = 'STOP'
            break

    if exit_reason is None:
        exit_price = float(df.iloc[exit_idx]['close'])
        exit_reason = 'EXIT'

    pnl = calc_pnl_rub('LONG', entry, exit_price, 1, cfg)
    comm = COMMISSION
    net_pnl = pnl - comm
    bars_held = (exit_idx - i) if exit_reason == 'EXIT' else (j - i)

    trades.append({
        'ticker': tk,
        'entry_time': str(sig['time']),
        'entry_price': entry,
        'exit_price': exit_price,
        'pnl': pnl,
        'commission': comm,
        'net_pnl': net_pnl,
        'bars_held': bars_held,
        'reason': exit_reason,
        'vol_z': sig['vol_z'],
        'yur_z': sig['yur_z'],
    })

# ── 3. Results ────────────────────────────────────────────────
print(f"\n[3] Results\n")

total_gross = sum(t['pnl'] for t in trades)
total_comm = sum(t['commission'] for t in trades)
total_net = total_gross - total_comm
total_ret = total_net / CAPITAL * 100

# Per ticker
by_ticker = {}
for t in trades:
    tk = t['ticker']
    by_ticker.setdefault(tk, {'trades': [], 'pnls': []})
    by_ticker[tk]['trades'].append(t)
    by_ticker[tk]['pnls'].append(t['net_pnl'])

print(f"{'Ticker':>6} {'Trades':>7} {'WR%':>5} {'Gross PnL':>10} {'Comm':>8} {'Net PnL':>10} {'Max DD%':>8} {'Avg Win':>9} {'Avg Loss':>9}")
print("-" * 80)

for tk in TICKERS:
    if tk not in by_ticker:
        print(f"{tk:>6} {'0':>7} {'N/A':>5} {'N/A':>10} {'N/A':>8} {'N/A':>10} {'N/A':>8} {'N/A':>9} {'N/A':>9}")
        continue
    d = by_ticker[tk]
    pnls = d['pnls']
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / n * 100 if n else 0
    gross = sum(t['pnl'] for t in d['trades'])
    comm = sum(t['commission'] for t in d['trades'])
    net = sum(pnls)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0

    # Calculate equity curve for this ticker
    eq = [CAPITAL]
    for p in pnls:
        eq.append(eq[-1] + p)
    mdd = max_dd_from_equity(eq) * 100

    print(f"{tk:>6} {n:>7} {wr:>5.1f} {gross:>+10.0f} {comm:>8.0f} {net:>+10.0f} {mdd:>7.2f}% {avg_win:>+9.0f} {avg_loss:>+9.0f}")

# Portfolio totals
print("\n" + "=" * 80)
print(f"{'PORTFOLIO':>6} {len(trades):>7} {sum(1 for t in trades if t['net_pnl']>0)/len(trades)*100:>5.1f} {total_gross:>+10.0f} {total_comm:>8.0f} {total_net:>+10.0f} {'':>8} {'':>9} {'':>9}")
print(f"  Return on capital: {total_ret:+.2f}%")
print(f"  Final PnL: {total_net:+.0f} RUB")

# Portfolio DD
port_equity = [CAPITAL]
for t in trades:
    port_equity.append(port_equity[-1] + t['net_pnl'])
port_mdd = max_dd_from_equity(port_equity) * 100
print(f"  Portfolio Max DD: {port_mdd:.2f}%")

# ── 4. Best/Worst trades ─────────────────────────────────────
print("\n[4] Best / Worst trades\n")

sorted_trades = sorted(trades, key=lambda t: t['net_pnl'], reverse=True)
print("  TOP 3 trades:")
for t in sorted_trades[:3]:
    d = f"vol_z={t['vol_z']:.1f} yur_z={t['yur_z']:.1f} held={t['bars_held']}b {t['reason']}"
    print(f"    {t['ticker']:>4} {t['entry_time']} PnL={t['net_pnl']:+8.0f} | {d}")

print("  WORST 3 trades:")
for t in sorted_trades[-3:]:
    d = f"vol_z={t['vol_z']:.1f} yur_z={t['yur_z']:.1f} held={t['bars_held']}b {t['reason']}"
    print(f"    {t['ticker']:>4} {t['entry_time']} PnL={t['net_pnl']:+8.0f} | {d}")

# Summary
print(f"\n  Total trades: {len(trades)}")
print(f"  Gross PnL: {total_gross:+.0f} RUB")
print(f"  Commission: {total_comm:.0f} RUB")
print(f"  Net PnL: {total_net:+.0f} RUB")
print(f"  Return: {total_ret:+.2f}%")
print(f"  Portfolio DD: {port_mdd:.2f}%")
print(f"\nDone.")
