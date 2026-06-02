#!/usr/bin/env python3
"""Compare Alor 5m data vs MOEX ISS daily data for Si to find discrepancies."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
from datetime import date, timedelta

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# 1. Check Si data volume by year
cur.execute("""
    SELECT DATE_PART('year', time) as yr, COUNT(*) as cnt,
           MIN(time)::date as first, MAX(time)::date as last,
           MIN(volume) as min_vol, MAX(volume) as max_vol, AVG(volume)::int as avg_vol
    FROM moex_prices_5m WHERE symbol = 'Si'
    GROUP BY yr ORDER BY yr
""")
print("=== Si 5m bars by year ===")
print(f"{'Year':6s} {'Bars':>8s} {'First':>12s} {'Last':>12s} {'MinVol':>8s} {'MaxVol':>8s} {'AvgVol':>8s}")
for r in cur.fetchall():
    print(f"{int(r[0]):6d} {r[1]:>8d} {str(r[2]):>12s} {str(r[3]):>12s} {r[4]:>8,} {r[5]:>8,} {r[6]:>8}")

# 2. Check data by contract
cur.execute("""
    SELECT contract, COUNT(*) as cnt, MIN(time)::date as first, MAX(time)::date as last,
           AVG(volume)::int as avg_vol
    FROM moex_prices_5m WHERE symbol = 'Si'
    GROUP BY contract ORDER BY contract
""")
print("\n=== Si by contract ===")
for r in cur.fetchall():
    print(f"  {r[0]:20s} {r[1]:>8d} bars  {str(r[2])}..{str(r[3])}  avg_vol={r[4]:>8,}")

# 3. Check for gaps - days with < expected 5m bars
cur.execute("""
    SELECT time::date as dt, COUNT(*) as cnt
    FROM moex_prices_5m
    WHERE symbol = 'Si' AND time >= '2025-06-01'
    GROUP BY time::date
    ORDER BY dt
""")
print("\n=== Si daily bar count (June 2025) ===")
min_correct = True
for r in cur.fetchall():
    status = "OK" if r[1] >= 60 else "SHORT!"
    if r[1] < 60:
        min_correct = False
        print(f"  {r[0]} {r[1]:>4d} bars  ⚠️ {status}")
    else:
        pass  # skip OK lines to keep output manageable
print(f"\n  All days >= 60 bars: {min_correct}")

# 4. Check other liquid tickers summary
cur.execute("""
    SELECT symbol, COUNT(*) as cnt, MIN(time)::date as first, MAX(time)::date as last
    FROM moex_prices_5m
    GROUP BY symbol
    ORDER BY symbol
""")
print("\n=== All tickers in moex_prices_5m ===")
for r in cur.fetchall():
    print(f"  {r[0]:12s} {r[1]:>8,} bars  {str(r[2])}..{str(r[3])}")

cur.close()
conn.close()
