#!/usr/bin/env python3
"""
Fast fill OI accounts for specific tickers.
Fetches futoi CSV year-by-year from MOEX ISS, bulk-upserts accounts columns.
"""
import sys, os, csv, io, time, logging
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REQUEST_TIMEOUT

import requests
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("oi_fill")

TICKERS = sys.argv[1:] if len(sys.argv) > 1 else [
    "Si", "Eu", "BR", "GD", "SR", "GZ", "VB", "ED", "GL", "NG",
    "CNYRUBF", "USDRUBF", "EURRUBF", "GLDRUBF", "IMOEXF", "SBERF", "GAZPF",
]

def get_db():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)

def fetch_year(ticker: str, yr: int) -> list[dict]:
    """Fetch one full year of OI data."""
    url = (
        f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
        f"{ticker}.csv?iss.meta=off&iss.only=futoi"
        f"&from={yr}-01-01&till={yr}-12-31&latest=0"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT * 3)
        if resp.status_code != 200:
            return []
        text = resp.text
        if "Invalid date" in text or "No data" in text or len(text) < 50:
            return []

        records = []
        for row in csv.reader(io.StringIO(text), delimiter=";"):
            if len(row) < 11:
                continue
            if row[0].strip() in ("futoi", "") or row[0] == "sess_id":
                continue
            clg = row[5].strip().upper()
            if clg not in ("FIZ", "YUR"):
                continue
            try:
                dt = datetime.strptime(f"{row[2].strip()} {row[3].strip()}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            try:
                ba = int(row[9]) if row[9].strip() else 0
                sa = int(row[10]) if row[10].strip() else 0
            except (ValueError, IndexError):
                continue
            if ba == 0 and sa == 0:
                continue
            records.append((ticker, dt, ba, sa, 0 if clg == "FIZ" else 1))
        return records
    except Exception as e:
        log.warning(f"  {ticker} {yr} error: {e}")
        return []

def upsert_batch(conn, rows: list) -> int:
    """Bulk upsert accounts data."""
    if not rows:
        return 0
    # Dedup
    seen = set()
    deduped = []
    for r in rows:
        key = (r[0], r[1], r[4])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    if not deduped:
        return 0

    with conn.cursor() as cur:
        execute_values(cur,
            """INSERT INTO openinterest_moex (symbol, time, buy_accounts, sell_accounts, clgroup)
               VALUES %s
               ON CONFLICT (symbol, time, clgroup)
               DO UPDATE SET
                   buy_accounts = GREATEST(EXCLUDED.buy_accounts, openinterest_moex.buy_accounts),
                   sell_accounts = GREATEST(EXCLUDED.sell_accounts, openinterest_moex.sell_accounts)""",
            deduped)
        n = cur.rowcount
    conn.commit()
    return n

def main():
    conn = get_db()
    total = 0
    for ticker in TICKERS:
        log.info(f"\n--- {ticker} ---")
        ticker_total = 0
        for yr in range(2021, 2027):
            records = fetch_year(ticker, yr)
            if records:
                n = upsert_batch(conn, records)
                ticker_total += n
                log.info(f"  {yr}: {len(records)} rows, {n} upserted")
                time.sleep(0.2)
            else:
                log.info(f"  {yr}: 0 rows")
        log.info(f"  {ticker}: {ticker_total} total upserted")
        total += ticker_total
    conn.close()
    log.info(f"\nDone: {total} rows upserted for {len(TICKERS)} tickers")

if __name__ == "__main__":
    main()
