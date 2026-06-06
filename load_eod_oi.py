#!/usr/bin/env python3
"""
EOD OI loader — loads end-of-day snapshots for a ticker from MOEX futoi API.
Uses 2-day chunks to avoid API 1000-row limit.
Only keeps the LAST update per day per client group (EOD snapshot).
"""
import sys, os, csv, io, time, logging
from datetime import datetime, timedelta, date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REQUEST_TIMEOUT

import requests
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("eod_oi")

# Incremental mode: by default only load missing data
# Set to True for initial full reload
DELETE_EXISTING = False

def get_db():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)

def fetch_eod(ticker: str, start: date, end: date) -> list[tuple]:
    """
    Fetch OI data for date range [start, end].
    Returns EOD snapshots: (symbol, date, clgroup, pos_long, pos_short, long_num, short_num)
    """
    url = (
        f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
        f"{ticker}.csv?iss.meta=off&iss.only=futoi"
        f"&from={start.isoformat()}&till={end.isoformat()}&latest=0"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT * 2)
        if resp.status_code != 200:
            return []
        text = resp.text
        if "Invalid date" in text or "No data" in text or len(text) < 50:
            return []

        # Group by date+clgroup, keep LAST (latest) entry = EOD snapshot
        eod = {}
        for row in csv.reader(io.StringIO(text), delimiter=";"):
            if len(row) < 11:
                continue
            if row[0].strip() in ("futoi", "") or row[0] == "sess_id":
                continue
            clg = row[5].strip().upper()
            if clg not in ("FIZ", "YUR"):
                continue
            try:
                dt_str = f"{row[2].strip()} {row[3].strip()}"
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            try:
                plong = int(row[7])
                pshort = abs(int(row[8]))
                lnum = int(row[9]) if row[9].strip() else 0
                snum = int(row[10]) if row[10].strip() else 0
            except (ValueError, IndexError):
                continue

            day = dt.date()
            clg_code = 0 if clg == "FIZ" else 1
            key = (day, clg_code)
            # Always update — last one wins (API returns newest first, so this gets the LAST update of each day)
            eod[key] = (ticker, dt, plong, pshort, lnum, snum, clg_code)

        return list(eod.values())
    except Exception as e:
        log.warning(f"  {ticker} {start}..{end} error: {e}")
        return []

def bulk_upsert(conn, rows: list[tuple]) -> int:
    """Upsert full OI rows with all columns."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        execute_values(cur,
            """INSERT INTO openinterest_moex
               (symbol, time, buy_orders, sell_orders, buy_accounts, sell_accounts, clgroup)
               VALUES %s
               ON CONFLICT (symbol, time, clgroup)
               DO UPDATE SET
                   buy_orders = EXCLUDED.buy_orders,
                   sell_orders = EXCLUDED.sell_orders,
                   buy_accounts = EXCLUDED.buy_accounts,
                   sell_accounts = EXCLUDED.sell_accounts""",
            rows)
        n = cur.rowcount
    conn.commit()
    return n

def load_ticker(conn, ticker: str):
    """Load OI data for one ticker.

    DELETE_EXISTING=True: full reload from 2021-01-01.
    DELETE_EXISTING=False (default): incremental — continue from last loaded date.
    """
    if DELETE_EXISTING:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM openinterest_moex WHERE symbol = %s", (ticker,))
            deleted = cur.rowcount
        conn.commit()
        log.info(f"  Deleted {deleted} existing rows")
        start = date(2021, 1, 1)
    else:
        # Find last date in DB
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(time)::date FROM openinterest_moex WHERE symbol = %s", (ticker,))
            r = cur.fetchone()
            last_in_db = r[0] if r and r[0] else date(2021, 1, 1)
        # Go back 2 days to catch any partial-day data
        start = last_in_db - timedelta(days=2)
        log.info(f"  Last in DB: {last_in_db}, resuming from {start}")
    today = date.today()
    total = 0

    # Process 2-day chunks
    current = start
    while current <= today:
        chunk_end = min(current + timedelta(days=1), today)  # 2 days: current, current+1
        records = fetch_eod(ticker, current, chunk_end)
        if records:
            n = bulk_upsert(conn, records)
            total += n
        current += timedelta(days=2)
        time.sleep(0.15)  # be polite

    log.info(f"  {ticker}: {total} EOD records loaded")
    return total

def main():
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["Si"]
    conn = get_db()
    grand_total = 0
    for ticker in tickers:
        log.info(f"\n--- {ticker} ---")
        n = load_ticker(conn, ticker)
        grand_total += n
    conn.close()
    log.info(f"\nDone: {grand_total} records for {tickers}")

if __name__ == "__main__":
    main()
