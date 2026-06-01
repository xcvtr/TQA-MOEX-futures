#!/usr/bin/env python3
"""
MOEX Price Snapshot Loader

Fetches current marketdata for all futures from ISS API
and saves prices (open, high, low, last, volume, OI) to PostgreSQL.

API: https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.only=marketdata
"""

import sys, os, json, time
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, MOEX_OI_TICKERS

import requests
import psycopg2
from psycopg2.extras import execute_values

TICKER_SET = set(MOEX_OI_TICKERS)
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2


def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )
    conn.autocommit = False
    return conn


def save_prices(conn, records: list[tuple]) -> int:
    """Save price records to DB. Returns count."""
    if not records:
        return 0

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO moex_prices (symbol, time, open, high, low, last, volume, open_interest, settle_price)
               VALUES %s
               ON CONFLICT (symbol, time) DO NOTHING""",
            records,
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


def fetch_all_marketdata() -> Optional[list[dict]]:
    """Fetch marketdata for all futures from ISS."""
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.meta=off&iss.only=marketdata"

    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code} (attempt {attempt + 1})")
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
                continue

            data = resp.json()
            cols = data["marketdata"]["columns"]
            rows = data["marketdata"]["data"]
            return [dict(zip(cols, row)) for row in rows]

        except Exception as e:
            print(f"  Error: {e} (attempt {attempt + 1})")
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)

    return None


def main():
    print(f"=== MOEX Price Snapshot [{datetime.now():%Y-%m-%d %H:%M:%S}] ===")

    conn = get_db()
    rows = fetch_all_marketdata()
    if not rows:
        print("No data received")
        conn.close()
        return

    now = datetime.now()
    records = []
    matched = 0

    for r in rows:
        secid = r.get("SECID", "")
        if secid not in TICKER_SET:
            continue
        matched += 1

        try:
            last = float(r["LAST"]) if r.get("LAST") not in (None, "", 0) else None
            open_ = float(r["OPEN"]) if r.get("OPEN") not in (None, "", 0) else None
            high = float(r["HIGH"]) if r.get("HIGH") not in (None, "", 0) else None
            low = float(r["LOW"]) if r.get("LOW") not in (None, "", 0) else None
            volume = int(r["VOLTODAY"]) if r.get("VOLTODAY") not in (None, "", 0) else None
            oi = int(r["OPENPOSITION"]) if r.get("OPENPOSITION") not in (None, "", 0) else None
            settle = float(r["SETTLEPRICE"]) if r.get("SETTLEPRICE") not in (None, "", 0) else None
        except (ValueError, TypeError):
            continue

        records.append((
            secid, now, open_, high, low, last, volume, oi, settle,
        ))

    inserted = save_prices(conn, records)
    print(f"  Tickers matched: {matched}, records saved: {inserted}")
    conn.close()
    print(f"=== Done ===")


if __name__ == "__main__":
    main()
