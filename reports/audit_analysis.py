#!/usr/bin/env python3
"""Аудит стратегии DOM-кластерного бэктеста — полный анализ."""

import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

PG = dict(host="10.0.0.64", port=5432, dbname="forex", user="postgres", password="postgres")
conn = psycopg2.connect(**PG)

# ============================================================
# 0. All runs for portfolio fitting analysis
# ============================================================
print("=" * 80)
print("0. RUNS OVERVIEW (run_id 18-25, Feb-May 2025)")
print("=" * 80)
runs_df = pd.read_sql("SELECT * FROM tester_runs WHERE id BETWEEN 18 AND 25 ORDER BY id", conn)
for _, r in runs_df.iterrows():
    print(f"  run_id={r['id']:2d} | {r['symbol']:8s} | {r['start_date']} - {r['end_date']} | "
          f"trades={r['total_trades']:4d} | won={r['won_trades']:4d} | WR={r['win_rate']:5.1f}% | "
          f"PnL={r['total_pnl']:8.1f}p | PF={r['profit_factor']:.2f} | params={r['params']}")

# ============================================================
# 1. Fade vs Trend analysis (run_id 19, 22, 25, 21)
# ============================================================
print("\n" + "=" * 80)
print("1. FADE vs TREND-FOLLOWING ANALYSIS")
print("=" * 80)

target_run_ids = [19, 22, 25, 21]

trades = pd.read_sql(
    "SELECT * FROM clusters WHERE run_id IN (19, 22, 25, 21) AND exit_time IS NOT NULL AND pnl_pips IS NOT NULL "
    "ORDER BY run_id, entry_time",
    conn
)
print(f"  Total closed trades loaded: {len(trades)}")

# For each trade: we have first_seen (cluster start) and entry_time
# entry_price is already there. We need prices at first_seen and entry_time
# Strategy: SHORT when cluster_type='long' (fade the long), LONG when cluster_type='short'
# Fade = price moved OPPOSITE to trade direction from first_seen to entry_time
# Trend = price moved SAME direction as trade from first_seen to entry_time

# We need to load bar prices for each symbol from PG
def load_symbol_prices(conn, symbol, start, end):
    """Load bar prices for a symbol from PG."""
    tbl = f"{symbol}_data"
    try:
        df = pd.read_sql(
            f"SELECT time AT TIME ZONE 'UTC' as t, price FROM {tbl} "
            f"WHERE time >= %s AND time < %s AND price > 0 ORDER BY time",
            conn, params=(start, end)
        )
        if df.empty:
            return pd.DataFrame()
        df['t'] = pd.to_datetime(df['t'], utc=True)
        df = df.set_index('t')
        return df
    except Exception as e:
        print(f"  WARN: Could not load prices for {symbol}: {e}")
        return pd.DataFrame()

symbol_map = {
    19: 'eurusd',
    22: 'gbpusd',
    25: 'usdjpy',
    21: 'xauusd',
}

# Load prices for each symbol
all_prices = {}
min_time = trades['first_seen'].min() - timedelta(days=1)
max_time = trades['exit_time'].max() + timedelta(days=1)

for run_id, symbol in symbol_map.items():
    p = load_symbol_prices(conn, symbol, min_time, max_time)
    if not p.empty:
        all_prices[run_id] = p
        print(f"  Loaded prices for {symbol}: {len(p)} bars")
    else:
        # Try CH
        pass

# For CH fallback, also load more data
import clickhouse_connect
ch = clickhouse_connect.get_client(host='10.0.0.60', port=8123, username='default', password='')

for run_id, symbol in symbol_map.items():
    if run_id in all_prices:
        continue
    sym_up = symbol.upper()
    pip_size = 0.01 if 'JPY' in sym_up or 'XAU' in sym_up else 0.0001
    pip_str = str(pip_size)
    try:
        df = ch.query_df(
            f"SELECT time, ROUND(price, {len(pip_str) - 2}) AS price FROM forex.bars "
            f"WHERE symbol = %(sym)s AND time >= %(start)s::DateTime64(3) "
            f"AND time < %(end)s::DateTime64(3) AND close > 0 "
            f"ORDER BY time",
            parameters={'sym': symbol.lower(), 'start': str(min_time), 'end': str(max_time)},
        )
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], utc=True)
            df = df.set_index('time')
            all_prices[run_id] = df[['price']]
            print(f"  Loaded CH prices for {symbol}: {len(df)} rows")
    except Exception as e:
        print(f"  WARN: CH also failed for {symbol}: {e}")

# Now classify each trade
results = []
for _, t in trades.iterrows():
    rid = t['run_id']
    sym = symbol_map[rid]
    cluster_type = t['cluster_type']  # 'long' or 'short'
    trade_dir = 'SHORT' if cluster_type == 'long' else 'LONG'
    entry_time = t['entry_time']
    first_seen = t['first_seen']
    pnl = t['pnl_pips']
    entry_price = t['entry_price']
    
    if rid not in all_prices:
        continue
    
    pr = all_prices[rid]
    
    # Get price at first_seen and entry_time
    price_first = None
    price_entry = None
    
    # Make tz-aware for comparison
    first_seen_utc = pd.Timestamp(first_seen).tz_localize('UTC') if first_seen.tzinfo is None else first_seen
    entry_time_utc = pd.Timestamp(entry_time).tz_localize('UTC') if entry_time.tzinfo is None else entry_time
    
    # Find nearest price at/before first_seen
    idx_first = pr.index.asof(first_seen_utc)
    if idx_first is not None and idx_first in pr.index:
        val = pr.loc[idx_first, 'price']
        price_first = float(val.iloc[0]) if hasattr(val, 'iloc') else float(val)
    
    idx_entry = pr.index.asof(entry_time_utc)
    if idx_entry is not None and idx_entry in pr.index:
        val = pr.loc[idx_entry, 'price']
        price_entry = float(val.iloc[0]) if hasattr(val, 'iloc') else float(val)
    
    if price_first is None or price_entry is None:
        continue
    
    # Price movement from first_seen to entry_time
    price_move = price_entry - price_first  # positive = price went up
    
    # If trade_dir is SHORT (fade long cluster), we want price to have gone UP (fade)
    # If price went up (price_move > 0) and we short = fade (correct)
    # If price went down (price_move < 0) and we short = trend-following (wrong)
    # If trade_dir is LONG (fade short cluster), we want price to have gone DOWN (fade)
    # If price went down (price_move < 0) and we long = fade (correct)
    # If price went up (price_move > 0) and we long = trend-following (wrong)
    
    if trade_dir == 'SHORT':
        is_fade = price_move > 0  # price rose, we short against the rise
    else:  # LONG
        is_fade = price_move < 0  # price fell, we long against the fall
    
    # Also compute: entry_price vs exit_price for pnl sign check
    bar_count = None
    if t['exit_time'] and t['entry_time']:
        diff = (t['exit_time'] - t['entry_time']).total_seconds() / 3600  # hours
        # For H1 timeframe
        bar_count = int(diff)  # approximate H1 bars
    
    results.append({
        'run_id': rid,
        'symbol': sym,
        'trade_dir': trade_dir,
        'cluster_type': cluster_type,
        'price_first': price_first,
        'price_entry': price_entry,
        'price_move_pips': round(price_move * (100 if 'jpy' in sym.lower() or 'xau' in sym.lower() else 10000), 1),
        'pnl': pnl,
        'is_fade': is_fade,
        'entry_time': entry_time,
        'exit_time': t['exit_time'],
        'hold_hours': diff if t['exit_time'] and t['entry_time'] else None,
    })

results_df = pd.DataFrame(results)
print(f"\n  Classified trades: {len(results_df)}")

# Convert is_fade to proper bool (in case any are numpy types)
results_df['is_fade'] = results_df['is_fade'].apply(lambda x: bool(x) if not isinstance(x, (bool, np.bool_)) else x)

# Per symbol stats
for rid in target_run_ids:
    sym = symbol_map[rid]
    sub = results_df[results_df['run_id'] == rid]
    if len(sub) == 0:
        continue
    fade = sub[sub['is_fade']]
    trend = sub[~sub['is_fade']]
    fade_pct = len(fade) / len(sub) * 100
    trend_pct = len(trend) / len(sub) * 100
    
    print(f"\n  --- {sym.upper()} (run_id={rid}) ---")
    print(f"    Всего сделок: {len(sub)}")
    print(f"    FADE (против движения кластера): {len(fade)} ({fade_pct:.1f}%)")
    print(f"    TREND (по движению кластера):    {len(trend)} ({trend_pct:.1f}%)")
    
    if len(fade) > 0:
        print(f"    Средний PnL FADE:   {fade['pnl'].mean():.1f}p (медиана: {fade['pnl'].median():.1f}p)")
        print(f"    WinRate FADE:       {len(fade[fade['pnl'] > 0])/len(fade)*100:.1f}%")
        print(f"    Сумма PnL FADE:    {fade['pnl'].sum():.1f}p")
    if len(trend) > 0:
        print(f"    Средний PnL TREND:  {trend['pnl'].mean():.1f}p (медиана: {trend['pnl'].median():.1f}p)")
        print(f"    WinRate TREND:      {len(trend[trend['pnl'] > 0])/len(trend)*100:.1f}%")
        print(f"    Сумма PnL TREND:   {trend['pnl'].sum():.1f}p")

# ============================================================
# 2. XAUUSD Monthly PnL + Hold time analysis
# ============================================================
print("\n" + "=" * 80)
print("2. XAUUSD DEEP ANALYSIS (run_id=21)")
print("=" * 80)

xau = trades[trades['run_id'] == 21].copy()
print(f"  Всего закрытых сделок XAUUSD: {len(xau)}")

# Monthly PnL
xau['month'] = xau['entry_time'].dt.to_period('M')
monthly = xau.groupby('month').agg(
    trades=('pnl_pips', 'count'),
    total_pnl=('pnl_pips', 'sum'),
    avg_pnl=('pnl_pips', 'mean'),
    win_rate=('pnl_pips', lambda x: (x > 0).mean() * 100),
    max_drawdown=('pnl_pips', lambda x: abs(x[x < 0].sum()) if (x < 0).any() else 0),
)
print(f"\n  XAUUSD PnL по месяцам:")
print(f"  {'Месяц':<12} {'Сделок':<8} {'Общий PnL':<12} {'Средний':<10} {'WinRate':<8} {'Max DD(сумма)'}")
print(f"  {'-'*60}")
for month, row in monthly.iterrows():
    print(f"  {str(month):<12} {row['trades']:<8} {row['total_pnl']:<+10.1f}p {row['avg_pnl']:<+8.1f}p {row['win_rate']:<7.1f}% {row['max_drawdown']:<+10.1f}p")

# Hold time analysis
xau['hold_bars'] = xau.apply(
    lambda r: (r['exit_time'] - r['entry_time']).total_seconds() / 3600 if pd.notna(r['exit_time']) else None, 
    axis=1
)

short_trades = xau[xau['hold_bars'] < 3]
medium_trades = xau[(xau['hold_bars'] >= 3) & (xau['hold_bars'] <= 10)]
long_trades = xau[xau['hold_bars'] > 10]

print(f"\n  XAUUSD PnL по времени удержания:")
print(f"  {'Категория':<20} {'Сделок':<8} {'Общий PnL':<12} {'Средний':<10} {'WinRate':<8} {'Median PnL'}")
print(f"  {'-'*60}")
for label, grp in [('Короткие (<3h)', short_trades), ('Средние (3-10h)', medium_trades), ('Длинные (>10h)', long_trades)]:
    if len(grp) > 0:
        print(f"  {label:<20} {len(grp):<8} {grp['pnl_pips'].sum():<+10.1f}p {grp['pnl_pips'].mean():<+8.1f}p {len(grp[grp['pnl_pips']>0])/len(grp)*100:<7.1f}% {grp['pnl_pips'].median():<+8.1f}p")

# ============================================================
# 5. Portfolio Fitting Analysis (all runs 18-25)
# ============================================================
print("\n" + "=" * 80)
print("5. PORTFOLIO FITTING ANALYSIS (all runs 18-25, Feb-May 2025)")
print("=" * 80)

all_runs_trades = pd.read_sql(
    "SELECT c.*, r.symbol FROM clusters c "
    "JOIN tester_runs r ON c.run_id = r.id "
    "WHERE c.run_id BETWEEN 18 AND 25 AND c.exit_time IS NOT NULL AND c.pnl_pips IS NOT NULL "
    "ORDER BY c.run_id, c.entry_time",
    conn
)
print(f"  Total closed trades across all runs: {len(all_runs_trades)}")

all_runs_trades['month'] = all_runs_trades['entry_time'].dt.to_period('M')

# PnL by month and symbol
pivot = all_runs_trades.pivot_table(
    index='month',
    columns='symbol',
    values='pnl_pips',
    aggfunc='sum',
    fill_value=0
)

# Also add total trades count
count_pivot = all_runs_trades.pivot_table(
    index='month',
    columns='symbol',
    values='pnl_pips',
    aggfunc='count',
    fill_value=0
)

print(f"\n  PnL по месяцам для каждой пары (фев-май 2025):")
print(f"  {'Месяц':<10}", end='')
for col in pivot.columns:
    print(f" {col:>10}", end='')
print(f" {'TOTAL':>10}")
print(f"  {'-' * (10 + 11 * (len(pivot.columns) + 1))}")

for month in sorted(pivot.index):
    print(f"  {str(month):<10}", end='')
    for col in pivot.columns:
        val = pivot.loc[month, col]
        print(f" {val:>+9.0f}p", end='')
    total = pivot.loc[month].sum()
    print(f" {total:>+9.0f}p")

print(f"\n  Всего (Feb-May):")
print(f"  {'Символ':<10} {'Total PnL':<12} {'% of total':<12} {'Сделок':<8} {'WinRate':<8} {'PF':<8}")
print(f"  {'-'*60}")
total_all = all_runs_trades['pnl_pips'].sum()
for sym in sorted(all_runs_trades['symbol'].unique()):
    s = all_runs_trades[all_runs_trades['symbol'] == sym]
    pnl = s['pnl_pips'].sum()
    wr = len(s[s['pnl_pips'] > 0]) / len(s) * 100
    wins = s[s['pnl_pips'] > 0]['pnl_pips'].sum()
    losses = abs(s[s['pnl_pips'] < 0]['pnl_pips'].sum())
    pf = wins / losses if losses > 0 else float('inf')
    print(f"  {sym:<10} {pnl:<+10.1f}p {pnl/total_all*100:<10.1f}% {len(s):<8} {wr:<7.1f}% {pf:<7.2f}")

print(f"  {'TOTAL':<10} {total_all:<+10.1f}p {100.0:<10.1f}%")

# Concentration check
symbol_pnl = all_runs_trades.groupby('symbol')['pnl_pips'].sum().sort_values(ascending=False)
top_pct = symbol_pnl.iloc[0] / total_all * 100
top2_pct = symbol_pnl.iloc[:2].sum() / total_all * 100
print(f"\n  Концентрация: топ-1 ({symbol_pnl.index[0]}) = {top_pct:.1f}% профита")
print(f"  Концентрация: топ-2 = {top2_pct:.1f}% профита")

# Check XAUUSD different params
print(f"\n  XAUUSD параметры (отличаются): th=3.0, mbv=3.0, bl_bars=15")
print(f"  XAUUSD PnL: {symbol_pnl.get('xauusd', 0):+.1f}p ({symbol_pnl.get('xauusd', 0)/total_all*100:.1f}% от общего)")

conn.close()
print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
