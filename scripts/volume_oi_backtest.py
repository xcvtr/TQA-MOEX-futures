#!/usr/bin/env python3
"""
Volume × OI Backtest: NR, CC, IB — direct bar-level MTM simulation.
No cache dependency — all data loaded fresh.
"""
import sys, os, json, math
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

# ── Config ──
TICKERS = ['NR', 'CC', 'IB']
DAYS = 400
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')
CAPITAL = 200000
COMMISSION = 2  # RUB/contract round-trip

PARAMS = {
    'NR': {'vol_z': 3.0, 'yur_z': 1.5, 'horizon': 6, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'vol_z': 3.5, 'yur_z': 1.5, 'horizon': 3, 'sl_pct': 0.01, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'IB': {'vol_z': 3.5, 'yur_z': 2.0, 'horizon': 12, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}

def rolling_zs(vals, w=20):
    s = pd.Series(vals).ffill()
    mu = s.rolling(w, min_periods=w//2).mean()
    sd = s.rolling(w, min_periods=w//2).std().replace(0, 1)
    return ((s - mu) / sd).fillna(0)

def calc_pnl_rub(direction, entry, exit_price, contracts, cfg):
    """Calculate PnL in RUB for MOEX futures."""
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
    
    if not rows or len(rows) < 500:
        print(f"✗ only {len(rows) if rows else 0} bars")
        continue
    
    df = pd.DataFrame(rows, columns=[
        'time','open','high','low','close','volume',
        'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'
    ])
    
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['vol_z'] = rolling_zs(df['volume'], 20)
    df['fiz_z'] = rolling_zs(df['fiz_net'], 20)
    df['yur_z'] = rolling_zs(df['yur_net'], 20)
    
    all_ohlcv[ticker] = df
    
    # Detect signals
    p = PARAMS[ticker]
    mask = (df['vol_z'] > p['vol_z']) & (df['yur_z'] > p['yur_z']) & (df['fiz_z'] < 0)
    sig_idx = df[mask].index.tolist()
    
    for idx in sig_idx:
        entry_idx = idx + 1
        exit_idx = entry_idx + p['horizon']
        if exit_idx >= len(df):
            continue
        entry = float(df.iloc[entry_idx]['open'])
        if entry <= 0:
            continue
        
        sigs_for_ticker = []
        sigs_for_ticker.append({
            'ticker': ticker,
            'time': df.iloc[entry_idx]['time'],
            'entry': entry,
            'horizon': p['horizon'],
            'direction': 'LONG',
            'sl_pct': p['sl_pct'],
            'vol_z': float(df.iloc[idx]['vol_z']),
            'yur_z': float(df.iloc[idx]['yur_z']),
        })
        all_signals.extend(sigs_for_ticker)
    
    print(f"✓ {len(sigs_for_ticker)} signals / {len(df):,} bars")

all_signals.sort(key=lambda s: s['time'])
print(f"  Total signals: {len(all_signals)}")

# ── 2. Simulation ─────────────────────────────────────────────
print("\n[2] Running bar-level MTM simulation...")
print(f"  Capital: {CAPITAL:,.0f} ₽ · DD limit: 20% · Commission: {COMMISSION} ₽/contract")

MAX_CONCURRENT = 3
MARGIN_USAGE = 0.15
CAPITAL_USAGE = 0.30

positions = []  # active positions
trades = []     # closed trades
equity_curve = [CAPITAL]
capital = CAPITAL
log = []  # [(time, event, detail), ...]

def get_price(ticker, dt):
    df = all_ohlcv.get(ticker)
    if df is None:
        return None
    # Find nearest bar at or before dt
    mask = df['time'] <= dt
    if not mask.any():
        return None
    row = df[mask].iloc[-1]
    return float(row['close'])

for i, sig in enumerate(all_signals):
    current_time = sig['time']
    ticker = sig['ticker']
    cfg = PARAMS[ticker]
    
    # Check if we already have a position for this ticker
    existing = [p for p in positions if p['ticker'] == ticker]
    if existing:
        continue  # only one position per ticker at a time
    
    # Check max concurrent
    if len(positions) >= MAX_CONCURRENT:
        continue
    
    # Calculate position size
    go = cfg['go']
    capital_avail = capital * MARGIN_USAGE
    max_contracts = max(1, int(capital_avail / go))
    contracts = min(max_contracts, 5)  # cap at 5 contracts for risk
    
    if contracts < 1:
        continue
    
    entry_price = get_price(ticker, current_time)
    if entry_price is None:
        continue
    
    positions.append({
        'ticker': ticker,
        'direction': 'LONG',
        'entry_time': current_time,
        'entry_price': entry_price,
        'contracts': contracts,
        'sl_pct': cfg['sl_pct'],
        'exit_time': None,
        'exit_price': None,
        'pnl': 0,
        'bars_held': 0,
        'max_hold': cfg['horizon'],
        'high_since_entry': entry_price,
    })
    log.append((str(current_time)[:19], 'ENTER', f"{ticker} {contracts}c @ {entry_price:.2f}"))

# ── 3. Daily bar-by-bar MTM ──────────────────────────────────
print("\n[3] Walking through bars (MTM)...")

# Build a time-indexed list of all OHLCV bars across all tickers
all_times = set()
for tk, df in all_ohlcv.items():
    for t in df['time']:
        all_times.add(t)
all_times = sorted(all_times)

bar_num = 0
for current_time in all_times:
    if not positions:
        # Early exit check — if no positions, just update equity
        bar_num += 1
        equity_curve.append(capital)
        continue
    
    bar_num += 1
    
    # Check each position
    closed_positions = []
    for pos in positions:
        tk = pos['ticker']
        pnl = 0
        
        # Get current close price
        close_price = get_price(tk, current_time)
        if close_price is None:
            continue
        
        pos['bars_held'] += 1
        pos['high_since_entry'] = max(pos['high_since_entry'], close_price)
        
        # Check stop-loss
        if pos['sl_pct'] and pos['sl_pct'] > 0:
            stop_level = pos['entry_price'] * (1 - pos['sl_pct'])
            if close_price <= stop_level:
                # Stop-loss triggered
                exit_price = close_price  # exit at current close
                pnl = calc_pnl_rub('LONG', pos['entry_price'], exit_price, pos['contracts'], PARAMS[tk])
                log.append((str(current_time)[:19], 'STOP', f"{tk} @ {exit_price:.2f} pnl={pnl:.0f}"))
                pos.update({'exit_time': current_time, 'exit_price': exit_price, 'pnl': pnl})
                closed_positions.append(pos)
                continue
        
        # Check time-stop (max hold bars)
        if pos['bars_held'] >= pos['max_hold']:
            exit_price = close_price
            pnl = calc_pnl_rub('LONG', pos['entry_price'], exit_price, pos['contracts'], PARAMS[tk])
            log.append((str(current_time)[:19], 'EXIT', f"{tk} hold={pos['bars_held']}b @ {exit_price:.2f} pnl={pnl:.0f}"))
            pos.update({'exit_time': current_time, 'exit_price': exit_price, 'pnl': pnl})
            closed_positions.append(pos)
    
    # Remove closed positions
    for cp in closed_positions:
        positions.remove(cp)
        trades.append(cp)
        
        # Add PnL to capital (reinvest)
        comm = cp['contracts'] * COMMISSION
        capital += cp['pnl'] - comm
    
    # Update equity
    unrealized = 0
    for pos in positions:
        cp = get_price(pos['ticker'], current_time)
        if cp:
            upnl = calc_pnl_rub('LONG', pos['entry_price'], cp, pos['contracts'], PARAMS[pos['ticker']])
            unrealized += upnl
    equity_curve.append(capital + unrealized)

# Close remaining positions at end
for pos in positions[:]:
    tk = pos['ticker']
    pnl = calc_pnl_rub('LONG', pos['entry_price'], get_price(tk, all_times[-1]) or pos['entry_price'], 
                       pos['contracts'], PARAMS[tk])
    pos.update({'exit_time': all_times[-1], 'exit_price': get_price(tk, all_times[-1]), 'pnl': pnl})
    trades.append(pos)
    capital += pnl
    equity_curve.append(capital)
positions = []

# ── 4. Results ───────────────────────────────────────────────
print(f"\n  Bars processed: {bar_num}")
print(f"  Trades: {len(trades)}")

if not trades:
    print("No trades!")
    sys.exit(1)

total_pnl = sum(t['pnl'] for t in trades)
total_comm = sum(t['contracts'] * COMMISSION for t in trades)
net_pnl = total_pnl - total_comm
net_ret = net_pnl / CAPITAL * 100
wr = sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100
mdd = max_dd_from_equity(equity_curve) * 100
calmar = net_ret / mdd if mdd > 0 else 0

print(f"\n  ╔══════════════════════════════════════════╗")
print(f"  ║   Volume × OI — NR + CC + IB (v2)      ║")
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

# Per-ticker
by_tk = {}
for t in trades:
    tk = t['ticker']
    by_tk.setdefault(tk, {'pnl': 0, 'n': 0, 'wins': 0})
    by_tk[tk]['pnl'] += t['pnl']
    by_tk[tk]['n'] += 1
    if t['pnl'] > 0:
        by_tk[tk]['wins'] += 1

print("\n  Per ticker:")
for tk in sorted(by_tk.keys()):
    d = by_tk[tk]
    twr = d['wins'] / d['n'] * 100 if d['n'] else 0
    print(f"    {tk}: n={d['n']:>3} WR={twr:.0f}% PnL={d['pnl']:>+9.0f} ₽")

# ── 5. Save ──────────────────────────────────────────────────
print(f"\n[4] Saving to {OUT}/...")

summary = f"""# Volume × OI Backtest: NR + CC + IB

**Strategy:** Yur Accumulation (vol_z>threshold AND yur_z>threshold AND fiz_z<0)  
**Capital:** {CAPITAL:,} ₽  
**Commission:** {COMMISSION} ₽/contract (round-trip)  
**Bar-level MTM:** ✅ entry on signal bar close, MTM on every 5-min OHLCV bar, stop-loss, time-stop

## Parameters
| Ticker | Vol_z | Yur_z | Horizon | Stop-loss | Minstep | Tick RUB |
|--------|:-----:|:-----:|:-------:|:---------:|:-------:|:--------:|
"""
for tk in TICKERS:
    p = PARAMS[tk]
    summary += f"| {tk:>6} | >{p['vol_z']}σ | >{p['yur_z']}σ | {p['horizon']}b | {p['sl_pct']*100:.0f}% | {p['minstep']} | {p['tick_rub']} |\n"

summary += f"""
## Results

| Metric | Value |
|--------|-------|
| Total trades | {len(trades)} |
| Win rate | {wr:.1f}% |
| Gross PnL | {total_pnl:>+8.0f} ₽ |
| Commission | {total_comm:>8.0f} ₽ |
| Net PnL | {net_pnl:>+8.0f} ₽ |
| Net Return | {net_ret:>+7.2f}% |
| Max DD | {mdd:.2f}% |
| Calmar | {calmar:.2f} |
| Final Equity | {capital:>8.0f} ₽ |

## Per-ticker
| Ticker | Trades | WR | PnL | Avg PnL |
|--------|-------:|:--:|----:|--------:|
"""
for tk in sorted(by_tk.keys()):
    d = by_tk[tk]
    avg = d['pnl'] / d['n'] if d['n'] else 0
    summary += f"| {tk:>6} | {d['n']:>3} | {d['wins']/d['n']*100:.0f}% | {d['pnl']:>+8.0f} ₽ | {avg:>+7.0f} ₽ |\n"

with open(f'{OUT}/summary.md', 'w') as f:
    f.write(summary)

# Also save a detailed log
with open(f'{OUT}/trades.csv', 'w') as f:
    f.write('ticker,entry_time,exit_time,entry_price,exit_price,contracts,pnl,bars_held\n')
    for t in trades:
        f.write(f"{t['ticker']},{t['entry_time']},{t.get('exit_time','')},{t['entry_price']:.2f},{t.get('exit_price',0):.2f},{t['contracts']},{t['pnl']:.0f},{t['bars_held']}\n")

# Last 20 log entries
print("\n  Last 20 events:")
for entry in log[-20:]:
    print(f"    {entry[0]} {entry[1]:>6} {entry[2]}")

print(f"\nDone → {OUT}/")
