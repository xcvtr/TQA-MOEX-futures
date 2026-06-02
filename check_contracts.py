#!/usr/bin/env python3
"""Check contract selection for Eu and BR."""
import sys, os
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
from collections import defaultdict

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# Check all contracts used for Eu
cur.execute("""
    SELECT DISTINCT contract, COUNT(*) as cnt,
           MIN(time)::date as first, MAX(time)::date as last,
           AVG(volume)::int as avg_vol
    FROM moex_prices_5m
    WHERE symbol = 'Eu'
    GROUP BY contract
    ORDER BY first
""")
print("=== Eu contracts in DB ===")
for r in cur.fetchall():
    print(f"  {r[0]:20s} cnt={r[1]:>6d}  {r[2]}..{r[3]}  avg_vol={r[4]:>8,}")

# May 29 contract breakdown
cur.execute("""
    SELECT time, contract, volume, open, close
    FROM moex_prices_5m
    WHERE symbol = 'Eu' AND time::date = '2026-05-29'
    ORDER BY time
""")
print("\n=== Eu on May 29 - contract breakdown ===")
ctr_cnt = defaultdict(int)
ctr_vol = defaultdict(float)
for r in cur.fetchall():
    ctr_cnt[r[1]] += 1
    ctr_vol[r[1]] += r[2]
print(f"  Total bars: {sum(ctr_cnt.values())}")
for c, n in sorted(ctr_cnt.items()):
    print(f"  {c:20s}: {n} bars, total_vol={ctr_vol[c]:>12,.0f}")

# Check Eu-6.26 vs Eu-9.26 volume comparison for May 29
cur.execute("""
    SELECT contract, SUM(volume) as total_vol, AVG(volume)::int as avg_vol
    FROM moex_prices_5m
    WHERE symbol = 'Eu' AND time::date = '2026-05-29'
    GROUP BY contract
""")
print("\n=== Eu volume comparison May 29 ===")
for r in cur.fetchall():
    print(f"  {r[0]:20s} total_vol={r[1]:>12,}  avg_vol={r[2]:>8,}")

# Check BR contracts
cur.execute("""
    SELECT DISTINCT contract, COUNT(*) as cnt,
           MIN(time)::date as first, MAX(time)::date as last
    FROM moex_prices_5m
    WHERE symbol = 'BR'
    GROUP BY contract
    ORDER BY first
""")
print("\n=== BR contracts in DB ===")
for r in cur.fetchall():
    print(f"  {r[0]:20s} cnt={r[1]:>6d}  {r[2]}..{r[3]}")

cur.execute("""
    SELECT time, contract, volume, open, close
    FROM moex_prices_5m
    WHERE symbol = 'BR' AND time::date = '2026-05-29'
    ORDER BY time
""")
print("\n=== BR on May 29 - contract breakdown ===")
ctr_cnt = defaultdict(int)
for r in cur.fetchall():
    ctr_cnt[r[1]] += 1
print(f"  Total bars: {sum(ctr_cnt.values())}")
for c, n in sorted(ctr_cnt.items()):
    print(f"  {c:20s}: {n} bars")

# Check: when does Eu-6.26 vs Eu-9.26 take over?
cur.execute("""
    SELECT time::date, contract, COUNT(*) as cnt
    FROM moex_prices_5m
    WHERE symbol = 'Eu' AND time >= '2026-05-01' AND time < '2026-06-01'
    GROUP BY time::date, contract
    ORDER BY time::date, contract
""")
print("\n=== Eu daily contract mix (May 2026) ===")
for r in cur.fetchall():
    cname = r[1].ljust(20)
    print(f"  {r[0]}  {cname} {r[2]} bars")

cur.close()
conn.close()
