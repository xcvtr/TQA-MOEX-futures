#!/usr/bin/env python3
"""Compare DB data vs fresh Alor fetch for ALL liquid tickers on a recent day."""
import sys, os
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
import requests, json
from datetime import datetime, timezone, date

JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# Get latest trading day with data
cur.execute("""
    SELECT time::date as dt FROM moex_prices_5m WHERE symbol = 'Si'
    AND time::date < CURRENT_DATE 
    ORDER BY dt DESC LIMIT 1
""")
ref_date = cur.fetchone()[0]
print(f"Reference date: {ref_date}")

# Get all tickers with their counts for that day
cur.execute("""
    SELECT symbol, COUNT(*) as cnt, MAX(time)::date as latest
    FROM moex_prices_5m
    WHERE time::date = %s OR time::date = %s
    GROUP BY symbol
    ORDER BY symbol
""", (ref_date, ref_date))
db_rows = cur.fetchall()
cur.close()
conn.close()

print(f"\n=== Ticker coverage for {ref_date} ===")
print(f"{'Symbol':12s} {'DB bars':>8s}  {'recent_date':>12s}")
liquid_tickers = set()
for r in db_rows:
    print(f"  {r[0]:12s} {r[1]:>8d}  {str(r[2]):>12s}")
    if r[1] >= 60:
        liquid_tickers.add(r[0])

print(f"\nLiquid tickers (>=60 bars): {len(liquid_tickers)}")

# For each liquid ticker, compare DB vs fresh Alor
from datetime import timedelta
ref_ts = int(datetime.combine(ref_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
ref_end = ref_ts + 86400

print(f"\n{'='*80}")
print(f"{'Comparing DB vs Alor for {ref_date}':^80}")
print(f"{'='*80}")
print(f"{'Symbol':12s} {'DB bars':>8s} {'Alor bars':>10s} {'Common':>7s} {'DB-Alor':>8s} {'Mismatch':>9s}")
print(f"{'-'*12:>12s} {'-'*8:>8s} {'-'*10:>10s} {'-'*7:>7s} {'-'*8:>8s} {'-'*9:>9s}")

# Ticker → Alor symbol mapping
TICKER_TO_ALOR = {
    "Si": "Si-6.26", "BR": "BR-6.26", "ED": "ED-6.26", "Eu": "Eu-6.26",
    "GD": "GOLD-6.26", "GZ": "GAZR-6.26", "SR": "SBRF-6.26", "VB": "VTBR-6.26",
    "CNYRUBF": "CNYRUBF", "USDRUBF": "USDRUBF",
}

for ticker in sorted(liquid_tickers):
    alor_sym = TICKER_TO_ALOR.get(ticker)
    if not alor_sym:
        continue
    
    # Fetch from Alor
    try:
        resp = requests.get(
            "https://api.alor.ru/md/v2/history",
            headers=HEADERS,
            params={"exchange": "MOEX", "symbol": alor_sym, "tf": 300,
                     "from": ref_ts, "to": ref_end},
            timeout=15
        )
        if resp.status_code != 200:
            print(f"  {ticker:12s} {'?':>8s} {'?':>10s}  Alor HTTP {resp.status_code}")
            continue
        alor_data = resp.json()
        alor_candles = alor_data.get("history", [])
        
        alor_by_time = {}
        for c in alor_candles:
            ts = datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None)
            alor_by_time[ts] = c
        
        # Fetch from DB
        conn2 = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur2 = conn2.cursor()
        cur2.execute("""
            SELECT time, open, high, low, close, volume
            FROM moex_prices_5m
            WHERE symbol = %s AND time::date = %s
            ORDER BY time
        """, (ticker, ref_date))
        db_rows2 = cur2.fetchall()
        cur2.close()
        conn2.close()
        
        db_by_time = {r[0]: r for r in db_rows2}
        common = set(db_by_time.keys()) & set(alor_by_time.keys())
        only_db = len(db_by_time) - len(common)
        only_alor = len(alor_by_time) - len(common)
        
        mismatches = 0
        for ts in list(common)[:200]:
            d = db_by_time[ts]
            a = alor_by_time[ts]
            if abs(d[1] - a["open"]) > 1 or abs(d[4] - a["close"]) > 1:
                mismatches += 1
        
        print(f"  {ticker:12s} {len(db_rows2):>8d} {len(alor_candles):>10d} {len(common):>7d} {only_db:>+8d} {mismatches:>9d}")
        
    except Exception as e:
        print(f"  {ticker:12s} ERROR: {e}")
