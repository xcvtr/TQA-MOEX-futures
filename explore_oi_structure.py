#!/usr/bin/env python3
"""Explore OI data structure for whale pattern analysis."""
import sys, os
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
from collections import defaultdict
from datetime import datetime, timedelta

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# 1. Table structure
cur.execute("""
    SELECT column_name, data_type FROM information_schema.columns
    WHERE table_name='openinterest_moex' ORDER BY ordinal_position
""")
print("=== Table columns ===")
for c in cur.fetchall():
    print(f"  {c[0]:25s} {c[1]}")

# 2. Sample data for Si
print("\n=== Si: sample rows (last 5 days) ===")
cur.execute("""
    SELECT time, clgroup, buy_orders as pos_long, sell_orders as pos_short,
           buy_accounts as long_num, sell_accounts as short_num
    FROM openinterest_moex
    WHERE symbol = 'Si' AND time >= NOW() - INTERVAL '5 days'
    ORDER BY time DESC LIMIT 10
""")
for r in cur.fetchall():
    cg = "FIZ" if r[1] == 0 else "YUR"
    print(f"  {r[0]} {cg:4s} long={r[2]:>8,} short={r[3]:>8,}  lnum={r[4]:>6,} snum={r[5]:>6,}")

# 3. How many updates per day for Si?
print("\n=== Si: updates per day (last 30 days) ===")
cur.execute("""
    SELECT time::date as dt, COUNT(*) as cnt
    FROM openinterest_moex WHERE symbol = 'Si'
    AND time >= NOW() - INTERVAL '30 days'
    GROUP BY time::date ORDER BY dt
""")
rows = cur.fetchall()
total_updates = sum(r[1] for r in rows)
days = len(rows)
print(f"  {days} days, {total_updates} total updates, avg {total_updates/days:.0f}/day")
for r in rows:
    print(f"  {r[0]} {r[1]:>4d} updates")

# 4. Diurnal pattern — updates per hour
print("\n=== Si: updates by hour ===")
cur.execute("""
    SELECT EXTRACT(HOUR FROM time) as hr, COUNT(*) as cnt
    FROM openinterest_moex WHERE symbol = 'Si'
    GROUP BY hr ORDER BY hr
""")
for r in cur.fetchall():
    print(f"  {int(r[0]):02d}:00 — {r[1]:>6d} updates")

# 5. End-of-day snapshot — last update per day for FIZ and YUR
print("\n=== Si: end-of-day snapshots (last 15 days) ===")
cur.execute("""
    SELECT DISTINCT ON (time::date, clgroup)
        time::date as dt, clgroup,
        buy_orders as pos_long, sell_orders as pos_short,
        buy_accounts as long_num, sell_accounts as short_num,
        time as last_update
    FROM openinterest_moex
    WHERE symbol = 'Si' AND time >= NOW() - INTERVAL '15 days'
    ORDER BY time::date, clgroup, time DESC
""")
print(f"{'Date':12s} {'Grp':4s} {'Long':>10s} {'Short':>10s} {'L_Num':>8s} {'S_Num':>8s}")
for r in cur.fetchall():
    cg = "FIZ" if r[1] == 0 else "YUR"
    print(f"  {r[0]} {cg:4s} {r[2]:>10,} {r[3]:>10,} {r[4]:>8,} {r[5]:>8,}")

# 6. Check all available symbols and their date ranges
print("\n=== All symbols: date range and row count ===")
cur.execute("""
    SELECT symbol, MIN(time)::date as first, MAX(time)::date as last, COUNT(*) as cnt
    FROM openinterest_moex GROUP BY symbol ORDER BY symbol
""")
for r in cur.fetchall():
    print(f"  {r[0]:12s} {str(r[1]):>12s} .. {str(r[2]):>12s}  {r[3]:>8,} rows")

cur.close()
conn.close()
