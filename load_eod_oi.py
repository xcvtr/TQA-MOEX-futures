#!/usr/bin/env python3
"""
EOD OI loader — loads end-of-day snapshots for a ticker from MOEX futoi API.
Uses 2-day chunks to avoid API 1000-row limit.
Only keeps the LAST update per day per client group (EOD snapshot).
Writes to ClickHouse (moex.openinterest).
"""
import sys, os, csv, io, time, logging
from datetime import datetime, timedelta, date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import CH_HOST, CH_PORT, CH_DB, REQUEST_TIMEOUT

import requests
import clickhouse_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("eod_oi")


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def fetch_eod(ticker: str, start: date, end: date) -> list[tuple]:
    """Fetch OI data for date range, return EOD snapshots."""
    url = (f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
           f"{ticker}.csv?iss.meta=off&iss.only=futoi"
           f"&from={start.isoformat()}&till={end.isoformat()}&latest=0")
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT * 2)
        if resp.status_code != 200:
            return []
        text = resp.text
        if "Invalid date" in text or "No data" in text or len(text) < 50:
            return []

        # Group by date+clgroup, keep LAST = EOD snapshot
        eod = {}
        for row in csv.reader(io.StringIO(text), delimiter=";"):
            if len(row) < 11: continue
            if row[0].strip() in ("futoi", "") or row[0] == "sess_id": continue
            clg = row[5].strip().upper()
            if clg not in ("FIZ", "YUR"): continue
            try:
                dt = datetime.strptime(f"{row[2].strip()} {row[3].strip()}", "%Y-%m-%d %H:%M:%S")
                bo = int(row[7]) if row[7].strip() else 0
                so = abs(int(row[8])) if row[8].strip() else 0
                ba = int(row[9]) if row[9].strip() else 0
                sa = int(row[10]) if row[10].strip() else 0
                cl = 0 if clg == "FIZ" else 1
            except ValueError:
                continue
            key = (dt.date(), cl)
            eod[key] = (dt, bo, so, ba, sa, cl)

        res = []
        for (d, cl), (dt, bo, so, ba, sa, clg) in eod.items():
            res.append((ticker, dt, bo, so, ba, sa, clg))
        return res
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
    tickers = sys.argv[1:] if len(sys.argv) > 1 else [
        "Si", "Eu", "BR", "GD", "SR", "GZ", "VB", "ED",
        "CNYRUBF", "USDRUBF", "IMOEXF",
    ]
    total = 0
    for ticker in tickers:
        start = date(2021, 1, 1)
        end = date.today()
        # Process in 2-day chunks to avoid API 1000-row limit
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=1), end)
            records = fetch_eod(ticker, cur, chunk_end)
            if records:
                save_batch(ch, records)
                total += len(records)
            cur += timedelta(days=2)
            time.sleep(0.05)
        log.info("%s: %d EOD records", ticker, total)
    log.info("Done: %d total EOD records", total)


if __name__ == "__main__":
    main()
