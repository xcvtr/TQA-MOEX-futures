#!/usr/bin/env python3
"""Backfill MOEX OI data for old years (2023-2025) into ClickHouse."""

import sys, os, csv, io, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CH_HOST, CH_PORT, CH_DB
import clickhouse_connect
from datetime import datetime, timedelta, date
import requests

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

tickers = sys.argv[1:] if len(sys.argv) > 1 else []
if not tickers:
    print("Usage: backfill_old.py TICKER1 TICKER2 ...")
    sys.exit(1)

total_ins = 0
for ticker in tickers:
    row = ch.query(
        "SELECT min(time) FROM moex.openinterest WHERE symbol = {t:String}",
        parameters={"t": ticker},
    ).result_rows
    first = row[0][0] if row and row[0][0] else None
    start = date(2023, 1, 1)
    end = first.date() - timedelta(days=1) if first else date(2025, 11, 11)

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
                        if k not in seen:
                            seen.add(k)
                            unique.append(r)
                    ch.insert(
                        "moex.openinterest",
                        unique,
                        column_names=["symbol", "time", "buy_orders", "sell_orders", "clgroup"],
                    )
                    ticker_ins += len(unique)
                    total_ins += len(unique)
        except Exception as e:
            pass
        current += timedelta(days=1)
        time.sleep(0.1)
    print(f"{ticker}: {ticker_ins} records")

print(f"Done: {total_ins} total records")
