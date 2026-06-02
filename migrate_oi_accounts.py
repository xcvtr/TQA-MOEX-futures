#!/usr/bin/env python3
"""
Migrate openinterest_moex: fill missing buy_accounts/sell_accounts.
Fetches futoi CSV from MOEX ISS for each ticker and upserts account columns.
Skips rows where buy_accounts > 0 (already filled).
"""
import sys, os, csv, io, time, logging
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    MOEX_OI_TICKERS, REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY,
)

import requests
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("oi_migrate")

HISTORY_START = date(2020, 12, 1)  # start slightly before first known data
BATCH_ROWS = 10000

def get_db():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    conn.autocommit = False
    return conn

def fetch_month(ticker: str, from_date: date, to_date: date) -> list[dict]:
    """Fetch OI data for a month — return only rows with account data."""
    url = (
        f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
        f"{ticker}.csv"
        f"?iss.meta=off&iss.only=futoi"
        f"&from={from_date.isoformat()}&till={to_date.isoformat()}&latest=0"
    )

    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
                timeout=REQUEST_TIMEOUT * 2,
            )
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                log.warning("  HTTP %d %s %s..%s", resp.status_code, ticker, from_date, to_date)
                time.sleep(RETRY_DELAY * 2)
                continue

            text = resp.text
            if "Invalid date" in text or "No data" in text:
                return []

            reader = csv.reader(io.StringIO(text), delimiter=";")
            records = []
            for row in reader:
                if len(row) < 11:
                    continue
                if row[0].strip() in ("futoi", "") or row[0] == "sess_id":
                    continue

                clgroup_raw = row[5].strip().upper()
                if clgroup_raw not in ("FIZ", "YUR"):
                    continue

                try:
                    dt_str = f"{row[2].strip()} {row[3].strip()}"
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue

                try:
                    buy_accounts = int(row[9]) if len(row) > 9 and row[9].strip() else 0
                    sell_accounts = int(row[10]) if len(row) > 10 and row[10].strip() else 0
                except (ValueError, IndexError):
                    continue

                # Only save rows that have account data
                if buy_accounts == 0 and sell_accounts == 0:
                    continue

                records.append({
                    "symbol": ticker,
                    "time": dt,
                    "buy_accounts": buy_accounts,
                    "sell_accounts": sell_accounts,
                    "clgroup": 0 if clgroup_raw == "FIZ" else 1,
                })

            return records

        except requests.RequestException as e:
            log.warning("  Network error %s %s..%s: %s", ticker, from_date, to_date, e)
            time.sleep(RETRY_DELAY * 2)

    return []

def upsert_accounts_batch(conn, records: list) -> int:
    """Bulk upsert account columns only."""
    if not records:
        return 0
    rows = []
    seen = set()
    for r in records:
        key = (r["symbol"], r["time"], r["clgroup"])
        if key not in seen:
            seen.add(key)
            rows.append((r["symbol"], r["time"], r["buy_accounts"], r["sell_accounts"], r["clgroup"]))

    if not rows:
        return 0

    with conn.cursor() as cur:
        execute_values(cur,
            """INSERT INTO openinterest_moex
               (symbol, time, buy_accounts, sell_accounts, clgroup)
               VALUES %s
               ON CONFLICT (symbol, time, clgroup)
               DO UPDATE SET buy_accounts = GREATEST(EXCLUDED.buy_accounts, openinterest_moex.buy_accounts),
                             sell_accounts = GREATEST(EXCLUDED.sell_accounts, openinterest_moex.sell_accounts)""",
            rows,
        )
        n = cur.rowcount
    conn.commit()
    return n

def main():
    # Check how many rows need fixing
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM openinterest_moex WHERE buy_accounts = 0 AND sell_accounts = 0")
    need_fix = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM openinterest_moex")
    total = cur.fetchone()[0]
    cur.close()
    log.info("=" * 60)
    log.info("OI ACCOUNTS MIGRATION")
    log.info(f"Total rows: {total:,}, missing accounts: {need_fix:,} ({need_fix/total*100:.1f}%)")
    log.info(f"Tickers: {len(MOEX_OI_TICKERS)}, from: {HISTORY_START}")
    log.info("=" * 60)

    total_loaded = 0
    for ticker in sorted(MOEX_OI_TICKERS):
        log.info(f"\n--- {ticker} ---")
        try:
            # Generate months
            current = date(HISTORY_START.year, HISTORY_START.month, 1)
            today = date.today()
            ticker_loaded = 0
            buffer = []

            while current <= today:
                # End of month
                if current.month == 12:
                    month_end = date(current.year + 1, 1, 1) - timedelta(days=1)
                else:
                    month_end = date(current.year, current.month + 1, 1) - timedelta(days=1)
                month_end = min(month_end, today)

                records = fetch_month(ticker, current, month_end)
                if records:
                    for r in records:
                        buffer.append(r)
                    if len(buffer) >= BATCH_ROWS:
                        n = upsert_accounts_batch(conn, buffer)
                        total_loaded += n
                        ticker_loaded += n
                        buffer = []
                time.sleep(0.15)

                # Next month — advance by 1 month
                if current.month == 12:
                    current = date(current.year + 1, 1, 1)
                else:
                    current = date(current.year, current.month + 1, 1)

            # Flush remaining
            if buffer:
                n = upsert_accounts_batch(conn, buffer)
                total_loaded += n
                ticker_loaded += n

            log.info(f"  {ticker}: {ticker_loaded} accounts upserted")
        except Exception as e:
            log.error(f"  {ticker} FAILED: {e}")
            conn.rollback()

    conn.close()
    log.info(f"\nDone: {total_loaded} account rows upserted")

if __name__ == "__main__":
    main()
