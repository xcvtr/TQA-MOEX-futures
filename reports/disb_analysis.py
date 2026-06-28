#!/usr/bin/env python3
"""Disbalance correlation analysis on ClickHouse MOEX data."""

import subprocess
import json
import sys

CH = ["clickhouse-client", "--host", "10.0.0.60", "--format", "JSONEachRow", "-q"]

def ch_query(sql):
    """Execute ClickHouse query, return list of dicts."""
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"CH ERROR: {r.stderr[:500]}", file=sys.stderr)
        return []
    if not r.stdout.strip():
        return []
    rows = []
    for line in r.stdout.strip().split('\n'):
        if line:
            rows.append(json.loads(line))
    return rows

def pearson_r(x_col, y_col, table, where, asset):
    sql = f"""
    SELECT
        corr(disb, next_return) AS r,
        count() AS n,
        avg(disb) AS avg_disb,
        avg(next_return) AS avg_ret
    FROM (
        SELECT
            asset_code,
            disb,
            LEAD(pr_close) OVER (PARTITION BY asset_code ORDER BY {x_col}) / pr_close - 1 AS next_return
        FROM {table}
        WHERE {where}
    )
    WHERE asset_code = '{asset}'
      AND disb IS NOT NULL
      AND next_return IS NOT NULL
      AND isFinite(next_return)
      AND abs(next_return) < 0.5
    """
    rows = ch_query(sql)
    if rows:
        return rows[0]
    return None

def compute_correlations_all_assets(table, time_col, where):
    sql = f"""
    SELECT
        asset_code,
        corr(disb, next_return) AS r,
        count() AS n,
        avg(disb) AS avg_disb,
        avg(next_return) AS avg_ret
    FROM (
        SELECT
            asset_code,
            disb,
            LEAD(pr_close) OVER (PARTITION BY asset_code ORDER BY {time_col}) / pr_close - 1 AS next_return
        FROM {table}
        WHERE {where}
    )
    WHERE disb IS NOT NULL
      AND next_return IS NOT NULL
      AND isFinite(next_return)
      AND abs(next_return) < 0.5
    GROUP BY asset_code
    HAVING n >= 100
    ORDER BY r DESC
    """
    rows = ch_query(sql)
    return rows

def disb_quantiles(table, time_col, where):
    sql = f"""
    SELECT
        multiIf(disb > 0.3, 'high_pos',
                disb > 0.1, 'mid_pos',
                disb > -0.1, 'neutral',
                disb > -0.3, 'mid_neg',
                'high_neg') AS bucket,
        count() AS n,
        avg(next_return) AS avg_ret,
        avg(disb) AS avg_disb
    FROM (
        SELECT
            disb,
            LEAD(pr_close) OVER (PARTITION BY asset_code ORDER BY {time_col}) / pr_close - 1 AS next_return
        FROM {table}
        WHERE {where} AND asset_code = 'Si'
    )
    WHERE disb IS NOT NULL
      AND next_return IS NOT NULL
      AND isFinite(next_return)
      AND abs(next_return) < 0.5
    GROUP BY bucket
    ORDER BY avg_disb
    """
    return ch_query(sql)

def strategy_test(table, time_col, where, threshold=0.3):
    sql = f"""
    SELECT
        countIf(signal = 'long') AS long_trades,
        countIf(signal = 'short') AS short_trades,
        avgIf(next_return * 100, signal = 'long') AS long_avg_ret_pct,
        abs(avgIf(next_return * 100, signal = 'short')) AS short_avg_absret_pct,
        countIf(signal = 'long' AND next_return > 0) * 100.0 / nullIf(countIf(signal = 'long'), 0) AS long_winrate,
        countIf(signal = 'short' AND next_return < 0) * 100.0 / nullIf(countIf(signal = 'short'), 0) AS short_winrate,
        avg(next_return * 100) FILTER(WHERE signal IS NOT NULL) AS all_avg_pnl_pct,
        countIf(signal IS NOT NULL AND next_return > 0) * 100.0 / nullIf(countIf(signal IS NOT NULL), 0) AS all_winrate
    FROM (
        SELECT *,
            multiIf(disb > {threshold}, 'long',
                    disb < -{threshold}, 'short',
                    NULL) AS signal
        FROM (
            SELECT
                disb,
                LEAD(pr_close) OVER (PARTITION BY asset_code ORDER BY {time_col}) / pr_close - 1 AS next_return
            FROM {table}
            WHERE {where} AND asset_code = 'Si'
        )
        WHERE disb IS NOT NULL
          AND next_return IS NOT NULL
          AND isFinite(next_return)
          AND abs(next_return) < 0.5
    )
    """
    rows = ch_query(sql)
    return rows

def strategy_test_all_assets(table, time_col, where, threshold=0.3, top_n=10):
    sql = f"""
    SELECT
        asset_code,
        countIf(signal = 'long') AS long_trades,
        countIf(signal = 'short') AS short_trades,
        avgIf(next_return * 100, signal = 'long') AS long_avg_ret_pct,
        abs(avgIf(next_return * 100, signal = 'short')) AS short_avg_absret_pct,
        countIf(signal = 'long' AND next_return > 0) * 100.0 / nullIf(countIf(signal = 'long'), 0) AS long_winrate,
        countIf(signal = 'short' AND next_return < 0) * 100.0 / nullIf(countIf(signal = 'short'), 0) AS short_winrate,
        avg(next_return * 100) FILTER(WHERE signal IS NOT NULL) AS avg_pnl_pct,
        countIf(signal IS NOT NULL AND next_return > 0) * 100.0 / nullIf(countIf(signal IS NOT NULL), 0) AS winrate
    FROM (
        SELECT *,
            multiIf(disb > {threshold}, 'long',
                    disb < -{threshold}, 'short',
                    NULL) AS signal
        FROM (
            SELECT
                asset_code,
                disb,
                LEAD(pr_close) OVER (PARTITION BY asset_code ORDER BY {time_col}) / pr_close - 1 AS next_return
            FROM {table}
            WHERE {where}
        )
        WHERE disb IS NOT NULL
          AND next_return IS NOT NULL
          AND isFinite(next_return)
          AND abs(next_return) < 0.5
    )
    GROUP BY asset_code
    HAVING long_trades + short_trades >= 30
    ORDER BY avg_pnl_pct DESC
    """
    rows = ch_query(sql)
    return rows[:top_n]

# ─── MAIN ────────────────────────────────────────────────────────────────────

print("=" * 70)
print("DISB CORRELATION ANALYSIS: disb[t] -> return[t+1]")
print("=" * 70)

# --- 1. Si correlation ---
print("\n--- 1. Si correlation: disb[t] -> return[t+1] ---")
for tf_name, table, time_col, where in [
    ("H1", "moex.tradestats_h1", "bar_hour", "bar_hour >= '2020-01-01'"),
    ("D1", "moex.tradestats_d1", "tradedate", "tradedate >= '2020-01-01'")
]:
    res = pearson_r(time_col, 'disb', table, where, 'Si')
    if res:
        print(f"  {tf_name}: r = {float(res['r']):+.5f}, n = {res['n']}, avg_disb={float(res['avg_disb']):+.4f}, avg_ret={float(res['avg_ret']):+.6f}")

# --- 2. Top assets by correlation ---
print("\n--- 2. Top-10 positive + bottom-10 negative correlation ---")
for tf_name, table, time_col, where in [
    ("H1", "moex.tradestats_h1", "bar_hour", "bar_hour >= '2020-01-01'"),
    ("D1", "moex.tradestats_d1", "tradedate", "tradedate >= '2020-01-01'")
]:
    print(f"\n  {tf_name}:")
    rows = compute_correlations_all_assets(table, time_col, where)
    if rows:
        print(f"  {'Asset':>8} {'r':>10} {'n':>8} {'avg_disb':>10} {'avg_ret':>10}")
        print(f"  {'-'*8} {'-'*10} {'-'*8} {'-'*10} {'-'*10}")
        for r in rows[:10]:
            print(f"  {r['asset_code']:>8} {float(r['r']):+10.5f} {r['n']:>8} {float(r['avg_disb']):+10.4f} {float(r['avg_ret']):+10.6f}")
        print(f"  {'...':>8} {'...':>10} {'...':>8} {'...':>10} {'...':>10}")
        for r in rows[-10:]:
            print(f"  {r['asset_code']:>8} {float(r['r']):+10.5f} {r['n']:>8} {float(r['avg_disb']):+10.4f} {float(r['avg_ret']):+10.6f}")
        # find Si
        for r in rows:
            if r['asset_code'] == 'Si':
                print(f"  [Si position: r={float(r['r']):+.5f}, rank from top: {rows.index(r)+1}/{len(rows)}]")
                break

# --- 3. Si lag analysis ---
print("\n--- 3. Si: Lag structure (disb[t] -> return[t+lag], lag=1..10) ---")
for tf_name, table, time_col, where in [
    ("H1", "moex.tradestats_h1", "bar_hour", "bar_hour >= '2020-01-01'"),
    ("D1", "moex.tradestats_d1", "tradedate", "tradedate >= '2020-01-01'")
]:
    print(f"  {tf_name}:")
    for lag in range(1, 11):
        sql = f"""
        SELECT
            corr(disb, future_return) AS r,
            count() AS n
        FROM (
            SELECT
                disb,
                pr_close,
                LEAD(pr_close, {lag}) OVER (PARTITION BY asset_code ORDER BY {time_col}) / pr_close - 1 AS future_return
            FROM {table}
            WHERE {where} AND asset_code = 'Si'
        )
        WHERE disb IS NOT NULL
          AND future_return IS NOT NULL
          AND isFinite(future_return)
          AND abs(future_return) < 0.5
        """
        rows = ch_query(sql)
        if rows and rows[0]['r'] is not None:
            print(f"    lag={lag}: r={float(rows[0]['r']):+.5f} (n={rows[0]['n']})")
        else:
            print(f"    lag={lag}: NULL")

# --- 4. Si: next_return by disb buckets ---
print("\n--- 4. Si: next_return by disb buckets ---")
for tf_name, table, time_col, where in [
    ("H1", "moex.tradestats_h1", "bar_hour", "bar_hour >= '2020-01-01'"),
    ("D1", "moex.tradestats_d1", "tradedate", "tradedate >= '2020-01-01'")
]:
    print(f"  {tf_name}:")
    print(f"  {'Bucket':>12} {'n':>8} {'avg_ret%':>10}")
    print(f"  {'-'*12} {'-'*8} {'-'*10}")
    rows = disb_quantiles(table, time_col, where)
    for r in rows:
        print(f"  {r['bucket']:>12} {r['n']:>8} {float(r['avg_ret'])*100:+9.4f}")

# --- 5. H1 vs D1 ---
print("\n--- 5. H1 vs D1: Overall correlation strength comparison ---")
h1_rows = compute_correlations_all_assets("moex.tradestats_h1", "bar_hour", "bar_hour >= '2020-01-01'")
d1_rows = compute_correlations_all_assets("moex.tradestats_d1", "tradedate", "tradedate >= '2020-01-01'")
h1_map = {r['asset_code']: float(r['r']) for r in h1_rows if r['r'] is not None}
d1_map = {r['asset_code']: float(r['r']) for r in d1_rows if r['r'] is not None}
h1_abs = [abs(v) for v in h1_map.values()]
d1_abs = [abs(v) for v in d1_map.values()]
print(f"  H1: avg|r|={sum(h1_abs)/len(h1_abs):.5f}, max|r|={max(h1_abs):.5f}, assets={len(h1_abs)}")
print(f"  D1: avg|r|={sum(d1_abs)/len(d1_abs):.5f}, max|r|={max(d1_abs):.5f}, assets={len(d1_abs)}")
common = [a for a in h1_map if a in d1_map]
print(f"  Common assets: {len(common)}")
h1_common_abs = [abs(h1_map[a]) for a in common]
d1_common_abs = [abs(d1_map[a]) for a in common]
print(f"  H1 avg|r| (common): {sum(h1_common_abs)/len(h1_common_abs):.5f}")
print(f"  D1 avg|r| (common): {sum(d1_common_abs)/len(d1_common_abs):.5f}")

# --- 6. Strategy test Si ---
print("\n--- 6. Strategy test: disb>+0.3 => long, disb<-0.3 => short, hold=1 bar (Si) ---")
for tf_name, table, time_col, where in [
    ("H1", "moex.tradestats_h1", "bar_hour", "bar_hour >= '2020-01-01'"),
    ("D1", "moex.tradestats_d1", "tradedate", "tradedate >= '2020-01-01'")
]:
    print(f"  {tf_name}:")
    rows = strategy_test(table, time_col, where)
    if rows:
        r = rows[0]
        print(f"    Long:  {r['long_trades']} trades, avg+{float(r['long_avg_ret_pct']):+.4f}%, winrate {float(r['long_winrate']):.1f}%")
        print(f"    Short: {r['short_trades']} trades, avg+{float(r['short_avg_absret_pct']):+.4f}%, winrate {float(r['short_winrate']):.1f}%")
        print(f"    Total: {int(r['long_trades'])+int(r['short_trades'])} trades, avg PnL {float(r['all_avg_pnl_pct']):+.4f}%, winrate {float(r['all_winrate']):.1f}%")

# --- 7. Strategy test Top assets ---
print("\n--- 7. Strategy test: Top-10 assets by signal performance ---")
for tf_name, table, time_col, where in [
    ("H1", "moex.tradestats_h1", "bar_hour", "bar_hour >= '2020-01-01'"),
    ("D1", "moex.tradestats_d1", "tradedate", "tradedate >= '2020-01-01'")
]:
    print(f"\n  {tf_name}:")
    rows = strategy_test_all_assets(table, time_col, where)
    if rows:
        print(f"  {'Asset':>8} {'Lng':>5} {'Shr':>5} {'LngRet%':>8} {'ShrRet%':>8} {'LngW%':>6} {'ShrW%':>6} {'AvgPnl%':>8} {'W%':>6}")
        print(f"  {'-'*8} {'-'*5} {'-'*5} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*8} {'-'*6}")
        for r in rows:
            print(f"  {r['asset_code']:>8} {r['long_trades']:>5} {r['short_trades']:>5} {float(r['long_avg_ret_pct']):+8.4f} {float(r['short_avg_absret_pct']):+8.4f} {float(r['long_winrate']):>5.1f}% {float(r['short_winrate']):>5.1f}% {float(r['avg_pnl_pct']):+8.4f} {float(r['winrate']):>5.1f}%")

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
