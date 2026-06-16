#!/usr/bin/env python3
"""
Bulk OI History Loader — загружает ВСЮ доступную историю futoi с 2021.
Читает все поля: pos_long, pos_short, pos_long_num, pos_short_num.
Пропускает уже загруженные даты (upsert).
Writes to ClickHouse (moex.openinterest).
"""
import sys, os, csv, io, time, logging
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    MOEX_OI_TICKERS, REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY,
    MOEX_LOGIN, MOEX_PASSWORD, CH_HOST, CH_PORT, CH_DB,
)

import requests
import clickhouse_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("bulk_oi")

HISTORY_START = date(2021, 1, 1)
BATCH_ROWS = 5000
FETCH_MONTHS = True


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def get_existing_dates(ch, ticker: str) -> set:
    """Get set of (date, clgroup) that have COMPLETE data in CH."""
    rows = ch.query(
        "SELECT DISTINCT toDate(time), clgroup FROM moex.openinterest "
        "WHERE symbol = {ticker:String} AND buy_accounts > 0 AND sell_accounts > 0",
        parameters={"ticker": ticker},
    ).result_rows
    return {(r[0], r[1]) for r in rows}


def save_batch(ch, rows: list[tuple]):
    """Insert OI records into CH."""
    if not rows:
        return
    ch.insert(
        "moex.openinterest",
        rows,
        column_names=["symbol", "time", "buy_orders", "sell_orders",
                       "buy_accounts", "sell_accounts", "clgroup"],
    )


def fetch_month(ticker: str, yr: int, mo: int) -> list[dict]:
    """Fetch one full month of OI data from MOEX ISS."""
    start = date(yr, mo, 1)
    if mo == 12:
        end = date(yr, 12, 31)
    else:
        end = date(yr, mo + 1, 1) - timedelta(days=1)

    url = (f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
           f"{ticker}.csv?iss.meta=off&iss.only=futoi"
           f"&from={start.isoformat()}&till={end.isoformat()}&latest=0")

    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT * 3)
            if resp.status_code != 200:
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
                    continue
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
                    ba = int(row[9]) if row[9].strip() else 0
                    sa = int(row[10]) if row[10].strip() else 0
                except ValueError:
                    continue
                records.append({
                    "time": dt, "buy_orders": bo, "sell_orders": so,
                    "buy_accounts": ba, "sell_accounts": sa,
                    "clgroup": 0 if clg == "FIZ" else 1,
                })
            return records
        except requests.RequestException as e:
            log.warning("Network error %s: %s", ticker, e)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
    return []


def load_ticker(ch, ticker: str):
    existing = get_existing_dates(ch, ticker)
    log.info("%s: %d existing date-clgroup pairs", ticker, len(existing))

    total = 0
    yr = HISTORY_START.year
    mo = HISTORY_START.month
    now = date.today()

    while date(yr, mo, 1) <= now:
        month_records = fetch_month(ticker, yr, mo)
        if month_records:
            to_insert = []
            for r in month_records:
                key = (r["time"].date(), r["clgroup"])
                if key not in existing:
                    to_insert.append((ticker, r["time"], r["buy_orders"],
                                       r["sell_orders"], r["buy_accounts"],
                                       r["sell_accounts"], r["clgroup"]))
                    if len(to_insert) >= BATCH_ROWS:
                        save_batch(ch, to_insert)
                        total += len(to_insert)
                        to_insert = []
            if to_insert:
                save_batch(ch, to_insert)
                total += len(to_insert)
            log.info("  %s %04d-%02d: %d records (%d new)", ticker, yr, mo, len(month_records), total)
        else:
            log.info("  %s %04d-%02d: 0 records", ticker, yr, mo)

        mo += 1
        if mo > 12:
            mo = 1
            yr += 1

        time.sleep(0.1)

    log.info("  %s: done, %d new records", ticker, total)
    return total


def main():
    ch = get_ch()
    total = 0
    for ticker in MOEX_OI_TICKERS:
        total += load_ticker(ch, ticker)
    log.info("=== Done: %d new records for %d tickers ===", total, len(MOEX_OI_TICKERS))


if __name__ == "__main__":
    main()
