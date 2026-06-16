#!/usr/bin/env python3
"""
Fast fill OI accounts for specific tickers.
Fetches futoi CSV year-by-year from MOEX ISS, bulk-upserts accounts columns.
Writes to ClickHouse (moex.openinterest).
"""
import sys, os, csv, io, time, logging
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import CH_HOST, CH_PORT, CH_DB, REQUEST_TIMEOUT

import requests
import clickhouse_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("oi_fill")

TICKERS = sys.argv[1:] if len(sys.argv) > 1 else [
    "Si", "Eu", "BR", "GD", "SR", "GZ", "VB", "ED", "GL", "NG",
    "CNYRUBF", "USDRUBF", "EURRUBF", "GLDRUBF", "IMOEXF", "SBERF", "GAZPF",
]


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def fetch_year(ticker: str, yr: int) -> list[dict]:
    url = (f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
           f"{ticker}.csv?iss.meta=off&iss.only=futoi"
           f"&from={yr}-01-01&till={yr}-12-31&latest=0")
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT * 3)
        if resp.status_code != 200:
            return []
        text = resp.text
        if "Invalid date" in text or "No data" in text or len(text) < 50:
            return []
        records = []
        for row in csv.reader(io.StringIO(text), delimiter=";"):
            if len(row) < 11: continue
            if row[0].strip() in ("futoi", "") or row[0] == "sess_id": continue
            clg = row[5].strip().upper()
            if clg not in ("FIZ", "YUR"): continue
            try:
                dt = datetime.strptime(f"{row[2].strip()} {row[3].strip()}", "%Y-%m-%d %H:%M:%S")
                bo = int(row[7]) if row[7].strip() else 0
                so = abs(int(row[8])) if row[8].strip() else 0
                ba = int(row[9]) if len(row) > 9 and row[9].strip() else 0
                sa = int(row[10]) if len(row) > 10 and row[10].strip() else 0
            except ValueError:
                continue
            records.append({
                "time": dt, "buy_orders": bo, "sell_orders": so,
                "buy_accounts": ba, "sell_accounts": sa,
                "clgroup": 0 if clg == "FIZ" else 1,
            })
        return records
    except requests.RequestException:
        return []


def save_batch(ch, rows):
    if not rows:
        return
    ch.insert(
        "moex.openinterest",
        rows,
        column_names=["symbol", "time", "buy_orders", "sell_orders",
                       "buy_accounts", "sell_accounts", "clgroup"],
    )


def main():
    ch = get_ch()
    total = 0
    for ticker in TICKERS:
        for yr in range(2021, 2026):
            records = fetch_year(ticker, yr)
            if not records:
                continue
            rows = [(ticker, r["time"], r["buy_orders"], r["sell_orders"],
                      r["buy_accounts"], r["sell_accounts"], r["clgroup"])
                    for r in records]
            save_batch(ch, rows)
            total += len(rows)
            log.info("%s %d: %d records loaded", ticker, yr, len(rows))
            time.sleep(0.15)
    log.info("Done: %d total records", total)


if __name__ == "__main__":
    main()
