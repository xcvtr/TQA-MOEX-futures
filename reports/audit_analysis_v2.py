#!/usr/bin/env python3
"""Аудит стратегии DOM-кластерного бэктеста — полный анализ (v2)."""

import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

PG = dict(host="10.0.0.64", port=5432, dbname="forex", user="postgres", password="postgres")
conn = psycopg2.connect(**PG)

symbol_map = {
    19: 'eurusd', 22: 'gbpusd', 25: 'usdjpy', 21: 'xauusd',
}

# ============================================================
# 0. ALL RUNS
# ============================================================
print("=" * 80)
print("0. RUNS OVERVIEW (run_id 18-25, Feb-May 2025)")
print("=" * 80)
runs = pd.read_sql("SELECT * FROM tester_runs WHERE id BETWEEN 18 AND 25 ORDER BY id", conn)
for _, r in runs.iterrows():
    print(f"  run_id={r['id']:2d} | {r['symbol']:8s} | {r['start_date']} - {r['end_date']} | "
          f"trades={r['total_trades']:4d} | WR={r['win_rate']:5.1f}% | PnL={r['total_pnl']:+8.1f}p | "
          f"PF={r['profit_factor']:.2f}")

# ============================================================
# 1. FADE vs TREND
# ============================================================
print("\n" + "=" * 80)
print("1. FADE vs TREND-FOLLOWING ANALYSIS")
print("=" * 80)

trades = pd.read_sql(
    "SELECT * FROM clusters WHERE run_id IN (19, 22, 25, 21) AND exit_time IS NOT NULL AND pnl_pips IS NOT NULL "
    "ORDER BY run_id, entry_time", conn
)
print(f"  Total closed trades: {len(trades)}")

# Use PG _data tables for prices (already loaded successfully earlier)
# PG has the data from the backtest bars
def load_pg_prices(conn, symbol, start, end):
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
        print(f"  WARN: {symbol}: {e}")
        return pd.DataFrame()

all_prices_m1 = {}
min_t = trades['first_seen'].min() - timedelta(days=2)
max_t = trades['exit_time'].max() + timedelta(days=1)

for run_id, sym in symbol_map.items():
    df = load_pg_prices(conn, sym, min_t, max_t)
    if not df.empty:
        all_prices_m1[run_id] = df
        print(f"  Loaded PG prices for {sym}: {len(df)} rows")

# For each trade: price change BEFORE cluster start (not from first_seen to entry_time)
# The logic: first_seen IS the entry_time (same bar), so we need to look BACK
# to see what price trend preceded the cluster formation
# We'll measure: price N bars before first_seen -> first_seen price
# to determine the pre-cluster trend (was price rising or falling before the cluster formed?)

results = []
for _, t in trades.iterrows():
    rid = t['run_id']
    sym = symbol_map[rid]
    cluster_type = t['cluster_type']
    trade_dir = 'SHORT' if cluster_type == 'long' else 'LONG'
    pnl = float(t['pnl_pips'])
    entry_price = float(t['entry_price'])
    first_seen = t['first_seen']
    entry_time = t['entry_time']
    exit_time = t['exit_time']
    
    if rid not in all_prices_m1:
        continue
    
    pr = all_prices_m1[rid]
    
    # Get price at first_seen bar (H1 bar right edge)
    first_seen_utc = pd.Timestamp(first_seen).tz_localize('UTC') if first_seen.tzinfo is None else pd.Timestamp(first_seen, tz='UTC')
    
    # Price just before cluster formation: look at bars -20 to -1 before first_seen
    # i.e. the 20 bars (H1 = 20 hours) before the cluster started
    idx_first = pr.index.asof(first_seen_utc)
    if idx_first is None or idx_first not in pr.index:
        continue
    
    # Get scalar value safely
    val_first = pr.loc[idx_first, 'price']
    price_at_first = float(val_first.iloc[0]) if hasattr(val_first, 'iloc') else float(val_first)
    
    # Find price ~20 bars before first_seen
    lookback = first_seen_utc - timedelta(hours=20)
    idx_lookback = pr.index.asof(lookback)
    if idx_lookback is None or idx_lookback not in pr.index:
        continue
    val_lb = pr.loc[idx_lookback, 'price']
    price_before = float(val_lb.iloc[0]) if hasattr(val_lb, 'iloc') else float(val_lb)
    
    # Pre-cluster price trend
    pre_move = price_at_first - price_before  # positive = price rose before cluster
    
    # Also check entry vs first_seen
    entry_utc = pd.Timestamp(entry_time).tz_localize('UTC') if entry_time.tzinfo is None else pd.Timestamp(entry_time, tz='UTC')
    idx_entry = pr.index.asof(entry_utc)
    if idx_entry is not None and idx_entry in pr.index:
        val = pr.loc[idx_entry, 'price']
        price_entry_actual = float(val.iloc[0]) if hasattr(val, 'iloc') else float(val)
    else:
        price_entry_actual = price_at_first
    
    # FADE definition (revised):
    # Cluster forms when crowd piles in. If price was RISING before cluster (pre_move > 0),
    # crowd is long (buying the breakout). Fade = go SHORT against this.
    # If price was FALLING before cluster (pre_move < 0), crowd is short. Fade = go LONG.
    # Trend-following = entering in the SAME direction as the pre-cluster move
    
    if trade_dir == 'SHORT':  # cluster is long, we short
        is_fade = pre_move > 0  # crowd bought the rise, we fade = short
    else:  # LONG, cluster is short, we buy
        is_fade = pre_move < 0  # crowd sold the dip, we fade = buy
    
    # Time-based: first_seen == entry_time almost always. Check the cluster age at entry
    hold_hours = (exit_time - entry_time).total_seconds() / 3600 if pd.notna(exit_time) and pd.notna(entry_time) else None
    
    results.append({
        'run_id': rid, 'symbol': sym,
        'trade_dir': trade_dir, 'cluster_type': cluster_type,
        'price_before': price_before, 'price_at_first': price_at_first,
        'pre_move_pips': round(pre_move * (100 if 'jpy' in sym or 'xau' in sym else 10000), 1),
        'pnl': pnl,
        'is_fade': is_fade,
        'entry_time': entry_time, 'exit_time': exit_time,
        'hold_hours': hold_hours,
    })

results_df = pd.DataFrame(results)
print(f"\n  Classified trades: {len(results_df)}")

print(f"\n  {'Symbol':<10} {'Total':<8} {'FADE':<8} {'FADE%':<8} {'TREND':<8} {'TREND%':<8} "
      f"{'FADE PnL':<12} {'TREND PnL':<12} {'FADE WR':<8} {'TREND WR':<8}")
print(f"  {'-'*84}")

for rid in [19, 22, 25, 21]:
    sym = symbol_map[rid]
    sub = results_df[results_df['run_id'] == rid]
    fade = sub[sub['is_fade']]
    trend = sub[~sub['is_fade']]
    f_pct = len(fade)/len(sub)*100 if len(sub) > 0 else 0
    t_pct = len(trend)/len(sub)*100 if len(sub) > 0 else 0
    f_pnl = fade['pnl'].mean() if len(fade) > 0 else 0
    t_pnl = trend['pnl'].mean() if len(trend) > 0 else 0
    f_wr = len(fade[fade['pnl']>0])/len(fade)*100 if len(fade) > 0 else 0
    t_wr = len(trend[trend['pnl']>0])/len(trend)*100 if len(trend) > 0 else 0
    f_sum = fade['pnl'].sum() if len(fade) > 0 else 0
    t_sum = trend['pnl'].sum() if len(trend) > 0 else 0
    print(f"  {sym:<10} {len(sub):<8} {len(fade):<8} {f_pct:<7.1f}% {len(trend):<8} {t_pct:<7.1f}% "
          f"{f_sum:<+10.1f}p / {f_pnl:<+5.1f}p {t_sum:<+10.1f}p / {t_pnl:<+5.1f}p "
          f"{f_wr:<6.1f}% {t_wr:<6.1f}%")

# ============================================================
# 2. XAUUSD Monthly + Hold time
# ============================================================
print("\n" + "=" * 80)
print("2. XAUUSD DEEP ANALYSIS (run_id=21)")
print("=" * 80)

xau = trades[trades['run_id'] == 21].copy()
print(f"  Всего закрытых сделок XAUUSD: {len(xau)}")

# Monthly
xau['month'] = xau['entry_time'].dt.to_period('M')
monthly = xau.groupby('month').agg(
    trades=('pnl_pips', 'count'),
    total_pnl=('pnl_pips', 'sum'),
    avg_pnl=('pnl_pips', 'mean'),
    win_rate=('pnl_pips', lambda x: (x > 0).mean() * 100),
)
print(f"\n  XAUUSD PnL по месяцам:")
print(f"  {'Месяц':<12} {'Сделок':<8} {'Общий PnL':<14} {'Средний':<12} {'WinRate':<10}")
print(f"  {'-'*56}")
for month, row in monthly.iterrows():
    print(f"  {str(month):<12} {row['trades']:<8.0f} {row['total_pnl']:<+10.1f}p {row['avg_pnl']:<+8.1f}p {row['win_rate']:<7.1f}%")

# Hold time
xau['hold_hours'] = (xau['exit_time'] - xau['entry_time']).dt.total_seconds() / 3600
short = xau[xau['hold_hours'] < 3]
medium = xau[(xau['hold_hours'] >= 3) & (xau['hold_hours'] <= 10)]
long_tr = xau[xau['hold_hours'] > 10]

print(f"\n  XAUUSD по времени удержания:")
print(f"  {'Категория':<20} {'Сделок':<8} {'Общий PnL':<14} {'Средний':<12} {'WinRate':<10} {'Median PnL'}")
print(f"  {'-'*66}")
for label, grp in [('Короткие (<3h)', short), ('Средние (3-10h)', medium), ('Длинные (>10h)', long_tr)]:
    if len(grp) > 0:
        print(f"  {label:<20} {len(grp):<8} {grp['pnl_pips'].sum():<+10.1f}p {grp['pnl_pips'].mean():<+8.1f}p "
              f"{len(grp[grp['pnl_pips']>0])/len(grp)*100:<7.1f}% {grp['pnl_pips'].median():<+8.1f}p")

# ============================================================
# 5. PORTFOLIO FITTING
# ============================================================
print("\n" + "=" * 80)
print("5. PORTFOLIO FITTING ANALYSIS (all runs 18-25)")
print("=" * 80)

all_trades = pd.read_sql(
    "SELECT c.*, r.symbol AS sym_name FROM clusters c "
    "JOIN tester_runs r ON c.run_id = r.id "
    "WHERE c.run_id BETWEEN 18 AND 25 AND c.exit_time IS NOT NULL AND c.pnl_pips IS NOT NULL "
    "ORDER BY c.run_id, c.entry_time", conn
)
print(f"  All closed trades: {len(all_trades)}")

all_trades['month'] = all_trades['entry_time'].dt.to_period('M')

# PnL by month + symbol
symbols_sorted = sorted(all_trades['sym_name'].unique())
print(f"\n  PnL по месяцам для каждой пары (фев-май 2025):")
header = "{:<10}".format("Месяц")
for sym in symbols_sorted:
    header += f" {sym:>8}"
header += f" {'TOTAL':>8}"
print(f"  {header}")
print(f"  {'-' * (10 + 9 * (len(symbols_sorted) + 1))}")

for month in sorted(all_trades['month'].unique()):
    line = f"  {str(month):<10}"
    for sym in symbols_sorted:
        mask = (all_trades['month'] == month) & (all_trades['sym_name'] == sym)
        pnl = all_trades.loc[mask, 'pnl_pips'].sum()
        line += f" {pnl:>+7.0f}p"
    total = all_trades[all_trades['month'] == month]['pnl_pips'].sum()
    line += f" {total:>+7.0f}p"
    print(line)

print(f"\n  {'Символ':<10} {'Total PnL':<14} {'% портфеля':<12} {'Сделок':<8} {'WinRate':<8} {'PF':<8}")
print(f"  {'-'*60}")
total_all = all_trades['pnl_pips'].sum()
for sym in symbols_sorted:
    s = all_trades[all_trades['sym_name'] == sym]
    pnl = s['pnl_pips'].sum()
    wr = len(s[s['pnl_pips'] > 0]) / len(s) * 100 if len(s) > 0 else 0
    wins = s[s['pnl_pips'] > 0]['pnl_pips'].sum()
    losses = abs(s[s['pnl_pips'] < 0]['pnl_pips'].sum())
    pf = wins / losses if losses > 0 else float('inf')
    print(f"  {sym:<10} {pnl:<+10.1f}p {pnl/total_all*100:<10.1f}% {len(s):<8} {wr:<7.1f}% {pf:<7.2f}")

print(f"  {'TOTAL':<10} {total_all:<+10.1f}p  {100.0:<10.1f}%")

# Concentration
sym_pnl = all_trades.groupby('sym_name')['pnl_pips'].sum().sort_values(ascending=False)
print(f"\n  Концентрация профита:")
for i, (sym, pnl) in enumerate(sym_pnl.items()):
    print(f"    {i+1}. {sym}: {pnl:+.1f}p ({pnl/total_all*100:.1f}%)")
print(f"  Топ-1 доля: {sym_pnl.iloc[0]/total_all*100:.1f}%")
print(f"  Топ-2 доля: {sym_pnl.iloc[:2].sum()/total_all*100:.1f}%")
print(f"  Все 8 пар {'плюсуют' if (sym_pnl > 0).all() else 'НЕ ВСЕ в плюсе'}: {(sym_pnl > 0).all()}")

conn.close()
print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
