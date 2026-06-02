#!/usr/bin/env python3
"""Detailed check of OI data quality for Si — daily snapshots + recent data."""
import sys, os
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
from datetime import date, timedelta

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# Si daily data — last day's data
print("=== Si: data distribution by year ===")
cur.execute("""
    SELECT EXTRACT(YEAR FROM time) as yr, clgroup, COUNT(*) as cnt
    FROM openinterest_moex WHERE symbol = 'Si'
    GROUP BY yr, clgroup ORDER BY yr, clgroup
""")
for r in cur.fetchall():
    cg = "FIZ" if r[1] == 0 else "YUR"
    print(f"  {int(r[0])} {cg:4s}: {r[2]:>8,} rows")

# Check: last date with data for Si
print("\n=== Si: last 5 dates ===")
cur.execute("""
    SELECT time::date as dt, COUNT(*) as cnt,
           SUM(CASE WHEN buy_accounts > 0 THEN 1 ELSE 0 END) as with_accts
    FROM openinterest_moex WHERE symbol = 'Si'
    GROUP BY time::date ORDER BY dt DESC LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]:>4d} updates, {r[2]} with accounts data")

# Check end-of-day FIZ/YUR for a specific recent day
cur.execute("""
    SELECT DISTINCT ON (clgroup)
        time::date as dt, clgroup,
        buy_orders, sell_orders,
        buy_accounts, sell_accounts,
        time as last_update
    FROM openinterest_moex
    WHERE symbol = 'Si' AND time::date = '2026-05-15'
    ORDER BY clgroup, time DESC
""")
print("\n=== Si: end-of-day 2026-05-15 ===")
for r in cur.fetchall():
    cg = "FIZ" if r[1] == 0 else "YUR"
    print(f"  {r[0]} {cg:4s} long={r[2]:>10,} short={r[3]:>10,} lnum={r[4]:>6,} snum={r[5]:>6,} last={r[6]}")

# Same for 2026-05-04 (first day available in last 15)
cur.execute("""
    SELECT DISTINCT ON (clgroup)
        time::date as dt, clgroup,
        buy_orders, sell_orders,
        buy_accounts, sell_accounts,
        time as last_update
    FROM openinterest_moex
    WHERE symbol = 'Si' AND time::date = '2026-05-04'
    ORDER BY clgroup, time DESC
""")
print("\n=== Si: end-of-day 2026-05-04 ===")
for r in cur.fetchall():
    cg = "FIZ" if r[1] == 0 else "YUR"
    print(f"  {r[0]} {cg:4s} long={r[2]:>10,} short={r[3]:>10,} lnum={r[4]:>6,} snum={r[5]:>6,} last={r[6]}")

# Check: 2024 data — sample
cur.execute("""
    SELECT time::date as dt, clgroup, COUNT(*) as cnt,
           MAX(buy_accounts) as max_lnum, MAX(sell_accounts) as max_snum
    FROM openinterest_moex
    WHERE symbol = 'Si' AND time::date = '2024-06-03'
    GROUP BY time::date, clgroup
""")
print("\n=== Si: 2024-06-03 ===")
for r in cur.fetchall():
    cg = "FIZ" if r[1] == 0 else "YUR"
    print(f"  {r[0]} {cg:4s} {r[2]:>4d} rows, max_lnum={r[3]:>6,} max_snum={r[4]:>6,}")

# Daily data coverage — check count of days with both FIZ and YUR
cur.execute("""
    SELECT COUNT(*) as tot_days,
           COUNT(*) FILTER (WHERE fiz_d > 0 AND yur_d > 0) as both_days
    FROM (
        SELECT time::date as dt,
               SUM(CASE WHEN clgroup=0 THEN 1 ELSE 0 END) as fiz_d,
               SUM(CASE WHEN clgroup=1 THEN 1 ELSE 0 END) as yur_d
        FROM openinterest_moex WHERE symbol = 'Si'
        GROUP BY time::date
    ) days
""")
r = cur.fetchone()
print(f"\n=== Si: daily coverage ===")
print(f"  Total days with data: {r[0]}")
print(f"  Days with both FIZ+YUR: {r[1]}")

# Daily coverage for last year
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE fiz_d > 0 AND yur_d > 0) as both_2025
    FROM (
        SELECT time::date as dt,
               SUM(CASE WHEN clgroup=0 THEN 1 ELSE 0 END) as fiz_d,
               SUM(CASE WHEN clgroup=1 THEN 1 ELSE 0 END) as yur_d
        FROM openinterest_moex WHERE symbol = 'Si' AND time >= '2025-01-01'
        GROUP BY time::date
    ) days
""")
r = cur.fetchone()
print(f"  2025+: days with both FIZ+YUR: {r[0]}")

cur.close()
conn.close()
