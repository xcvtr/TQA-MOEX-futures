#!/usr/bin/env python3 -u
"""Populate continuous infrastructure — _best_secid, _daily_best_secid, ticker_mapping."""
import sys, re, clickhouse_connect as cc

CH = dict(host='10.0.0.60', port=8123, database='moex')
ch = cc.get_client(**CH)

# 1. ticker_mapping — from price_5m contract prefixes
print("=== 1. Populating ticker_mapping ===")
rows = ch.query("""
    SELECT DISTINCT symbol, contract
    FROM moex.prices_5m
    WHERE contract NOT LIKE 'GEN_%%'
      AND contract NOT LIKE '%%TEST%%'
      AND contract NOT LIKE '%%RUBF%%'
      AND time >= '2020-01-01'
    ORDER BY symbol, contract
""").result_rows

prefix_to_symbol = {}
for sym, contract in rows:
    if not contract or not sym:
        continue
    prefix = re.match(r'^[A-Za-z]+', str(contract))
    if prefix:
        p = prefix.group().upper()
        if p not in prefix_to_symbol:
            # Map to PG ticker via ticker_specs if possible
            prefix_to_symbol[p] = sym

# Insert into ticker_mapping
ch.command("TRUNCATE TABLE moex.ticker_mapping")
batch = [(p, s) for p, s in prefix_to_symbol.items()]
if batch:
    ch.insert('moex.ticker_mapping', batch, column_names=['secid_prefix', 'ticker'])
print(f"  Inserted {len(batch)} mappings")

# 2. _best_secid — current best contract by volume (last 30 days)
print("\n=== 2. Populating _best_secid ===")
# For each symbol, find contract with highest volume in last 30 days
rows = ch.query("""
    SELECT symbol, argMax(contract, volume) as best_cont, sum(volume) as total_vol, count(DISTINCT contract) as n_cont
    FROM (
        SELECT symbol, contract, sum(volume) as volume
        FROM moex.prices_5m
        WHERE time >= now() - INTERVAL 30 DAY
          AND contract NOT LIKE 'GEN_%%'
          AND contract NOT LIKE '%%TEST%%'
          AND contract NOT LIKE '%%RUBF%%'
        GROUP BY symbol, contract
    )
    GROUP BY symbol
    HAVING total_vol > 0
    ORDER BY symbol
""").result_rows

ch.command("TRUNCATE TABLE moex._best_secid")
best_batch = [(r[0], r[1], r[2], r[3]) for r in rows]
if best_batch:
    ch.insert('moex._best_secid', best_batch, column_names=['ticker', 'best_secid', 'vol', 'n_secids'])
print(f"  Inserted {len(best_batch)} best contracts")

# Show the ones we care about
print("\n  MT5-relevant best contracts:")
for r in rows:
    if r[0] in ['BR','CR','GD','GZ','MM','NG','RN','Si']:
        print(f"    {r[0]:4s} -> {r[1]:12s} vol={r[2]:>12d}")

# 3. _daily_best_secid — daily best contract
print("\n=== 3. Populating _daily_best_secid ===")
ch.command("TRUNCATE TABLE moex._daily_best_secid")

# For each day and symbol, find contract with most volume
# Do it in batches by month to avoid timeout
from datetime import datetime, timedelta
import time

start_date = datetime(2020, 1, 1)
end_date = datetime.now()
current = start_date

total_inserted = 0
while current < end_date:
    next_month = current + timedelta(days=31)
    cutoff = next_month.isoformat()[:10]
    current_str = current.isoformat()[:10]
    
    try:
        daily_rows = ch.query("""
            SELECT toDate(time) as tradedate, symbol, argMax(contract, volume) as best_cont
            FROM (
                SELECT time, symbol, contract, sum(volume) as volume
                FROM moex.prices_5m
                WHERE time >= %(start)s AND time < %(end)s
                  AND contract NOT LIKE 'GEN_%%'
                  AND contract NOT LIKE '%%TEST%%'
                  AND contract NOT LIKE '%%RUBF%%'
                GROUP BY time, symbol, contract
            )
            GROUP BY tradedate, symbol
            ORDER BY tradedate, symbol
        """, parameters={'start': current_str, 'end': cutoff}).result_rows
        
        if daily_rows:
            daily_batch = [(r[0], r[1], r[2]) for r in daily_rows]
            ch.insert('moex._daily_best_secid', daily_batch, 
                     column_names=['tradedate', 'ticker', 'best_secid'])
            total_inserted += len(daily_batch)
            print(f"  {current_str}..{cutoff}: {len(daily_rows)} rows (total: {total_inserted})", flush=True)
    except Exception as e:
        print(f"  {current_str}: error: {e}", flush=True)
    
    current = next_month
    time.sleep(0.1)  # rate limit

print(f"\n  Total daily records: {total_inserted}")
ch.close()
print("Done!")
