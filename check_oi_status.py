#!/usr/bin/env python3
"""Check OI database status — columns, coverage, volume."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# Columns
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='openinterest_moex' ORDER BY ordinal_position")
print("=== Columns ===")
for c in cur.fetchall():
    print(f"  {c[0]:25s} {c[1]}")

# Symbol coverage
cur.execute("""
    SELECT symbol, COUNT(*) as cnt,
           MIN(time)::date as first, MAX(time)::date as last,
           SUM(CASE WHEN buy_accounts > 0 THEN 1 ELSE 0 END) as with_accounts,
           SUM(CASE WHEN pos_long_num > 0 THEN 1 ELSE 0 END) as with_posnum
    FROM openinterest_moex
    GROUP BY symbol
    ORDER BY symbol
""")
print("\n=== Symbol coverage ===")
print(f"{'Symbol':12s} {'Rows':>8s} {'First':>12s} {'Last':>12s} {'w/Accounts':>10s} {'w/PosNum':>10s}")
total_rows = 0
for r in cur.fetchall():
    print(f"{r[0]:12s} {r[1]:>8d} {str(r[2]):>12s} {str(r[3]):>12s} {r[4]:>10d} {r[5]:>10d}")
    total_rows += r[1]
print(f"\nTotal rows: {total_rows}")
print(f"Symbols: {cur.rowcount}")

# Check for pos_long_num/pos_short_num columns
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name='openinterest_moex' AND column_name IN ('pos_long_num','pos_short_num')
""")
has_posnum = cur.fetchall()
print(f"\npos_long_num/pos_short_num columns exist: {len(has_posnum) > 0}")
if has_posnum:
    print(f"  Columns: {[c[0] for c in has_posnum]}")

# Sample row with pos data
if has_posnum:
    cur.execute("""
        SELECT time::date, symbol, clgroup, pos_long, pos_short, pos_long_num, pos_short_num,
               buy_accounts, sell_accounts, buy_members, sell_members
        FROM openinterest_moex
        WHERE pos_long_num > 0 AND symbol = 'Si'
        ORDER BY time DESC LIMIT 5
    """)
    print("\n=== Sample Si rows with pos_long_num ===")
    for r in cur.fetchall():
        print(f"  {r[0]} {r[1]:6s} {r[2]:4s} long={r[3]:>10s} short={r[4]:>10s} lnum={r[5]:>6s} snum={r[6]:>6s} accts={r[7]:>6s}/{r[8]:>6s} members={r[9]:>6s}/{r[10]:>6s}")

cur.close()
conn.close()
