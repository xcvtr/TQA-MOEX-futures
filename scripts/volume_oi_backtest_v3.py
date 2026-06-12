#!/usr/bin/env python3
"""
Volume × OI Backtest v3: NR, CC, IB — proper chronological bar walk-through,
open-on-signal, close-on-time/stop, reinvest.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

OUT = 'reports/volume_oi_backtest'
os.makedirs(OUT, exist_ok=True)

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

TICKERS = ['NR', 'CC', 'IB']
DAYS = 400
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')
CAPITAL = 200000
COMMISSION = 2

PARAMS = {
    'NR': {'vol_z': 3.0, 'yur_z': 1.5, 'h': 6, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'vol_z': 3.5, 'yur_z': 1.5, 'h': 3, 'sl_pct': 0.01, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'IB': {'vol_z': 3.5, 'yur_z': 2.0, 'h': 12, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}

def rolling_zs(vals, w=20):
    s = pd.Series(vals).ffill()
    mu = s.rolling(w, min_periods=w//2).mean()
    sd = s.rolling(w, min_periods=w//2).std().replace(0, 1)
    return ((s - mu) / sd).fillna(0)

# ── 1. Load ───────────────────────────────────────────────────
print("[1] Loading data...")
ohlcv = {}
signals_by_time = {}

for ticker in TICKERS:
    print(f"  {ticker}...", end=' ', flush=True)
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m AS p
        INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0 AND o.total_oi > 0
        ORDER BY p.time
    """, parameters={'t': ticker, 's': since}).result_rows
    
    df = pd.DataFrame(rows, columns=[
        'time','open','high','low','close','volume',
        'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'
    ])
    
    dp = PARAMS[ticker]
    
    # Indicators
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['vol_z'] = rolling_zs(df['volume'], 20)
    df['fiz_z'] = rolling_zs(df['fiz_net'], 20)
    df['yur_z'] = rolling_zs(df['yur_net'], 20)
    
    # Detect
    mask = (df['vol_z'] > dp['vol_z']) & (df['yur_z'] > dp['yur_z']) & (df['fiz_z'] < 0)
    sig_idx = df[mask].index.tolist()
    
    signals = []
    for idx in sig_idx:
        entry_idx = idx + 1
        exit_idx = entry_idx + dp['h']
        if exit_idx >= len(df):
            continue
        t = df.iloc[entry_idx]['time']
        entry = float(df.iloc[entry_idx]['open'])
        if entry <= 0:
            continue
        signals.append({
            'ticker': ticker,
            'time': t,
            'entry': entry,
            'h': dp['h'],
            'sl_pct': dp['sl_pct'],
            'vol_z': float(df.iloc[idx]['vol_z']),
            'yur_z': float(df.iloc[idx]['yur_z']),
        })
        # Also store by time for quick lookup
        t_key = str(t)
        signals_by_time.setdefault(t_key, []).append(signals[-1])
    
    ohlcv[ticker] = df
    print(f"✓ {len(signals)} signals")

# ── 2. Simulate ──────────────────────────────────────────────
print("\n[2] Simulating bar-by-bar...")
print(f"  Capital: {CAPITAL:,.0f} ₽ · Commission: {COMMISSION} ₽/contract")

def close_price(ticker, dt):
    """Get close price at or before dt for a ticker."""
    df = ohlcv.get(ticker)
    if df is None:
        return None
    mask = df['time'] <= dt
    if not mask.any():
        return None
    return float(df[mask].iloc[-1]['close'])

def calc_pnl(ticker, direction, entry, exit_price, contracts):
    cfg = PARAMS[ticker]
    moves = (exit_price - entry) / cfg['minstep']
    if direction == 'SHORT':
        moves = -moves
    return moves * cfg['tick_rub'] * contracts

# Build unified bar timeline
all_bars = sorted(set(
    t for row in signals_by_time.values() for s in row
    for df in [ohlcv[s['ticker']]]
    for t in [s['time']]
))
# Actually just scan all bars from CC (most data)
timeline = ohlcv['CC']['time'].tolist()

capital = CAPITAL
equity_curve = [capital]

# Per-ticker state: is position open, entry details, bars held
pos = {tk: None for tk in TICKERS}

# Pre-compute exit times for each signal
print("  Pre-computing exits...")
signal_exits = {}  # ticker -> list of (entry_time, entry_price, exit_time, exit_price, h, sl_pct)
for tk in TICKERS:
    df = ohlcv[tk]
    dp = PARAMS[tk]
    mask = (df['vol_z'] > dp['vol_z']) & (df['yur_z'] > dp['yur_z']) & (df['fiz_z'] < 0)
    sig_idx = df[mask].index.tolist()
    
    exits = []
    for idx in sig_idx:
        entry_idx = idx + 1
        exit_idx = entry_idx + dp['h']
        if exit_idx >= len(df):
            continue
        entry_time = df.iloc[entry_idx]['time']
        entry = float(df.iloc[entry_idx]['open'])
        exit_time = df.iloc[exit_idx]['time']
        exit_px = float(df.iloc[exit_idx]['close'])
        if entry <= 0 or exit_px <= 0:
            continue
        # Stop level
        sl_level = entry * (1 - dp['sl_pct'])
        
        # Check if stop was hit between entry and exit
        mid_mask = (df['time'] > entry_time) & (df['time'] <= exit_time)
        mid_lows = df[mid_mask]['low']
        hit_stop = (mid_lows <= sl_level).any() if len(mid_lows) > 0 else False
        
        if hit_stop:
            # Find first bar where low <= stop
            stop_bar = df[mid_mask & (df['low'] <= sl_level)].iloc[0] if len(df[mid_mask & (df['low'] <= sl_level)]) > 0 else None
            if stop_bar is not None:
                actual_exit = stop_bar['close']
                actual_exit_time = stop_bar['time']
            else:
                actual_exit = sl_level
                actual_exit_time = exit_time
        else:
            actual_exit = exit_px
            actual_exit_time = exit_time
        
        pnl = calc_pnl(tk, 'LONG', entry, actual_exit, 1)  # per contract
        
        exits.append({
            'ticker': tk,
            'entry_time': entry_time,
            'entry': entry,
            'exit_time': actual_exit_time,
            'exit': actual_exit,
            'pnl_per_contract': pnl,
            'hit_stop': hit_stop,
        })
    
    signal_exits[tk] = exits
    print(f"  {tk}: {len(exits)} trade windows")

# ── 3. Portfolio sim with reinvest ───────────────────────────
print("\n[3] Running portfolio simulation...")

# Merge all exits into one timeline
all_events = []
for tk, exits in signal_exits.items():
    for e in exits:
        all_events.append(('entry', e))
        # Add exit event separately
        all_events.append(('exit', e))

# Sort by entry time
all_events.sort(key=lambda x: x[1]['entry_time'])

trades = []
positions = {}  # ticker -> active position
entry_capital = CAPITAL

for evt_type, evt in all_events:
    tk = evt['ticker']
    
    if evt_type == 'entry':
        # Only enter if no position on this ticker
        if tk in positions:
            continue
        
        # Size: reinvested capital
        go = PARAMS[tk]['go']
        cap_per_trade = capital * 0.15  # 15% of current capital per trade
        contracts = max(1, int(cap_per_trade / go))
        contracts = min(contracts, 5)
        
        positions[tk] = {
            'ticker': tk,
            'entry': evt['entry'],
            'entry_time': evt['entry_time'],
            'contracts': contracts,
        }
        
    elif evt_type == 'exit':
        if tk not in positions:
            continue
        
        pos = positions.pop(tk)
        pnl_per = evt['pnl_per_contract']
        pnl = pnl_per * pos['contracts']
        comm = pos['contracts'] * COMMISSION
        net = pnl - comm
        
        trades.append({
            'ticker': tk,
            'entry_time': str(pos['entry_time'])[:19],
            'exit_time': str(evt['exit_time'])[:19],
            'entry': round(pos['entry'], 2),
            'exit': round(evt['exit'], 2),
            'contracts': pos['contracts'],
            'pnl': round(pnl, 2),
            'commission': comm,
            'net_pnl': round(net, 2),
        })
        
        capital += net

# ── 4. Results ───────────────────────────────────────────────
total_pnl = sum(t['pnl'] for t in trades)
total_comm = sum(t['commission'] for t in trades)
net_pnl = total_pnl - total_comm
net_ret = (capital - entry_capital) / entry_capital * 100
wr = sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100 if trades else 0

# Simple DD estimation from trade PnL sequence
eq = [entry_capital]
for t in trades:
    eq.append(eq[-1] + t['net_pnl'])
mdd = 0
peak = eq[0]
for v in eq:
    if v > peak:
        peak = v
    dd = (peak - v) / peak if peak > 0 else 0
    mdd = max(mdd, dd)
mdd *= 100
calmar = net_ret / mdd if mdd > 0 else 0

print(f"\n  ╔══════════════════════════════════════════╗")
print(f"  ║   Volume × OI — NR + CC + IB (v3)      ║")
print(f"  ╠══════════════════════════════════════════╣")
print(f"  ║ Trades:    {len(trades):>3} ({wr:.0f}% WR)               ║")
print(f"  ║ Gross PnL: {total_pnl:>+9.0f} ₽          ║")
print(f"  ║ Comm:      {total_comm:>9.0f} ₽          ║")
print(f"  ║ Net PnL:   {net_pnl:>+9.0f} ₽          ║")
print(f"  ║ Net Ret:   {net_ret:>+7.2f}%               ║")
print(f"  ║ Max DD:    {mdd:.2f}%                  ║")
print(f"  ║ Calmar:    {calmar:.2f}                    ║")
print(f"  ║ Final Eq:  {capital:>9.0f} ₽          ║")
print(f"  ╚══════════════════════════════════════════╝")

by_tk = {}
for t in trades:
    tk = t['ticker']
    by_tk.setdefault(tk, {'pnl': 0, 'n': 0, 'wins': 0})
    by_tk[tk]['pnl'] += t['net_pnl']
    by_tk[tk]['n'] += 1
    if t['pnl'] > 0:
        by_tk[tk]['wins'] += 1

print("\n  Per ticker:")
for tk in sorted(by_tk.keys()):
    d = by_tk[tk]
    twr = d['wins'] / d['n'] * 100 if d['n'] else 0
    print(f"    {tk}: n={d['n']:>3} WR={twr:.0f}% PnL={d['pnl']:>+9.0f} ₽")

# Detailed log
print("\n  All trades:")
for t in trades:
    print(f"    {t['ticker']:>6} {t['entry_time']}→{t['exit_time'][-8:]} "
          f"entry={t['entry']:>7.2f} exit={t['exit']:>7.2f} "
          f"c={t['contracts']} pnl={t['pnl']:>+8.0f} comm={t['commission']:.0f} net={t['net_pnl']:>+8.0f}")

# ── 5. Save ──────────────────────────────────────────────────
print(f"\n[4] Saving...")
with open(f'{OUT}/trades_v3.csv', 'w') as f:
    f.write('ticker,entry_time,exit_time,entry,exit,contracts,pnl,commission,net_pnl\n')
    for t in trades:
        f.write(f"{t['ticker']},{t['entry_time']},{t['exit_time']},{t['entry']},{t['exit']},{t['contracts']},{t['pnl']:.0f},{t['commission']:.0f},{t['net_pnl']:.0f}\n")

summary = f"""# Volume × OI Backtest v3: NR + CC + IB

**Strategy:** Yur Accumulation · **Capital:** {CAPITAL:,} ₽ · **Comm:** {COMMISSION} ₽/contract  
**Bar-level:** entry on next-bar open, exit on horizon close or stop-loss hit

## Results
| Metric | Value |
|--------|-------|
| Trades | {len(trades)} |
| Win rate | {wr:.1f}% |
| Gross PnL | {total_pnl:>+8.0f} ₽ |
| Commission | {total_comm:>8.0f} ₽ |
| Net PnL | {net_pnl:>+8.0f} ₽ |
| Return | {net_ret:>+7.2f}% |
| Max DD | {mdd:.2f}% |
| Calmar | {calmar:.2f} |
| Final Eq | {capital:>8.0f} ₽ |

## Per Ticker
| Ticker | Trades | WR | Net PnL |
|--------|-------:|:--:|--------:|
"""
for tk in sorted(by_tk.keys()):
    d = by_tk[tk]
    summary += f"| {tk:>6} | {d['n']:>3} | {d['wins']/d['n']*100:.0f}% | {d['pnl']:>+8.0f} ₽ |\n"

with open(f'{OUT}/summary_v3.md', 'w') as f:
    f.write(summary)

print(f"Done → {OUT}/summary_v3.md")
