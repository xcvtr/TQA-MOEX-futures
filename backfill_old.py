#!/usr/bin/env python3
"""Backfill MOEX OI data for old years (2023-2025)."""

import sys, os, csv, io, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timedelta, date
import requests

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
conn.autocommit = False
cur = conn.cursor()

tickers = sys.argv[1:] if len(sys.argv) > 1 else []
if not tickers:
    print("Usage: backfill_old.py TICKER1 TICKER2 ...")
    sys.exit(1)

total_ins = 0
for ticker in tickers:
    cur.execute("SELECT MIN(time)::date FROM openinterest_moex WHERE symbol = %s", (ticker,))
    first = cur.fetchone()[0]
    start = date(2023, 1, 1)
    end = first - timedelta(days=1) if first else date(2025, 11, 11)
    
    if start >= end:
        print(f"{ticker}: already have data from {first}, skipping")
        continue
    
    current = start
    ticker_ins = 0
    days = (end - start).days
    print(f"{ticker}: {days} days ({start} -> {end})")
    
    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        url = f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/{ticker}.csv?iss.meta=off&iss.only=futoi&from={current}&till={current}&latest=0"
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if resp.status_code == 200 and "Invalid date" not in resp.text and "No data" not in resp.text:
                reader = csv.reader(io.StringIO(resp.text), delimiter=";")
                rows = []
                for row in reader:
                    if len(row) < 11 or row[0] in ("futoi","","sess_id"): continue
                    cl = row[5].strip().upper()
                    if cl not in ("FIZ","YUR"): continue
                    try:
                        dt = datetime.strptime(f"{row[2].strip()} {row[3].strip()}", "%Y-%m-%d %H:%M:%S")
                    except: continue
                    try:
                        buy = int(row[7]) if row[7].strip() else 0
                        sell = abs(int(row[8])) if row[8].strip() else 0
                    except: continue
                    rows.append((ticker, dt, buy, sell, 0 if cl=="FIZ" else 1))
                if rows:
                    seen = set()
                    unique = []
                    for r in rows:
                        k = (r[0], r[1], r[4])
                        if k not in seen: seen.add(k); unique.append(r)
                    execute_values(cur,
                        "INSERT INTO openinterest_moex (symbol, time, buy_orders, sell_orders, clgroup) VALUES %s ON CONFLICT (symbol, time, clgroup) DO UPDATE SET buy_orders = EXCLUDED.buy_orders, sell_orders = EXCLUDED.sell_orders",
                        unique)
                    conn.commit()
                    n = cur.rowcount
                    ticker_ins += n
                    total_ins += n
        except Exception as e:
            conn.rollback()
        current += timedelta(days=1)
        time.sleep(0.1)
    print(f"{ticker}: {ticker_ins} records")

print(f"Done: {total_ins} total records")
conn.close()
