#!/usr/bin/env python3
"""
Migrate openinterest_moex: fill missing buy_accounts/sell_accounts in ClickHouse.
Fetches futoi CSV from MOEX ISS for each ticker and upserts account columns.
Skips rows where buy_accounts > 0 (already filled).
"""
import sys, os, csv, io, time, logging
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    MOEX_OI_TICKERS, REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY,
    CH_HOST, CH_PORT, CH_DB,
)

import requests
import clickhouse_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("oi_migrate")

HISTORY_START = date(2020, 12, 1)
BATCH_ROWS = 10000


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def fetch_month(ticker: str, from_date: date, to_date: date) -> list[dict]:
    url = (f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
           f"{ticker}.csv?iss.meta=off&iss.only=futoi"
           f"&from={from_date.isoformat()}&till={to_date.isoformat()}&latest=0")

    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
                                timeout=REQUEST_TIMEOUT * 2)
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                log.warning("  HTTP %d %s %s..%s", resp.status_code, ticker, from_date, to_date)
                time.sleep(RETRY_DELAY * 2)
                continue
            text = resp.text
            if "Invalid date" in text or "No data" in text:
                return []
            records = []
            for row in csv.reader(io.StringIO(text), delimiter=";"):
                if len(row) < 11: continue
                if row[0].strip() in ("futoi", "") or row[0] == "sess_id": continue
                clg = row[5].strip().upper()
                if clg not in ("FIZ", "YUR"): continue
                try:
                    dt = datetime.strptime(f"{row[2].strip()} {row[3].strip()}", "%Y-%m-%d %H:%M:%S")
                    ba = int(row[9]) if len(row) > 9 and row[9].strip() else 0
                    sa = int(row[10]) if len(row) > 10 and row[10].strip() else 0
                except (ValueError, IndexError):
                    continue
                if ba == 0 and sa == 0:
                    continue
                records.append({"symbol": ticker, "time": dt,
                                "buy_accounts": ba, "sell_accounts": sa,
                                "clgroup": 0 if clg == "FIZ" else 1})
            return records
        except requests.RequestException as e:
            log.warning("  Network error %s %s..%s: %s", ticker, from_date, to_date, e)
            time.sleep(RETRY_DELAY * 2)
    return []


def upsert_accounts_batch(ch, records: list) -> int:
    """Bulk upsert account columns in CH."""
    if not records:
        return 0
    seen = set()
    rows = []
    for r in records:
        key = (r["symbol"], r["time"], r["clgroup"])
        if key not in seen:
            seen.add(key)
            rows.append((r["symbol"], r["time"], r["buy_accounts"], r["sell_accounts"], r["clgroup"]))

    if not rows:
        return 0

    rows_for_insert = []
    for rec in records:
        key = (rec["symbol"], rec["time"], rec["clgroup"])
        if key not in seen:
            seen.add(key)
        # CH ReplacingMergeTree — insert directly, dedup by ORDER BY
        rows_for_insert.append((rec["symbol"], rec["time"],
                                 rec["buy_accounts"], rec["sell_accounts"],
                                 rec["clgroup"]))

    ch.insert(
        "moex.openinterest",
        rows_for_insert,
        column_names=["symbol", "time", "buy_accounts", "sell_accounts", "clgroup"],
    )
    return len(rows_for_insert)


def main():
    ch = get_ch()

    # Check how many rows need fixing
    total_count = ch.query("SELECT count() FROM moex.openinterest").result_rows[0][0]
    need_fix = ch.query("SELECT count() FROM moex.openinterest WHERE buy_accounts = 0 AND sell_accounts = 0").result_rows[0][0]

    log.info("=" * 60)
    log.info("OI ACCOUNTS MIGRATION (ClickHouse)")
    log.info(f"Total rows: {total_count:,}, missing accounts: {need_fix:,} ({need_fix/total_count*100:.1f}%)")
    log.info(f"Tickers: {len(MOEX_OI_TICKERS)}, from: {HISTORY_START}")
    log.info("=" * 60)

    total_loaded = 0
    for ticker in sorted(MOEX_OI_TICKERS):
        log.info(f"\n--- {ticker} ---")
        try:
            current = date(HISTORY_START.year, HISTORY_START.month, 1)
            today = date.today()
            ticker_loaded = 0
            buffer = []

            while current <= today:
                if current.month == 12:
                    month_end = date(current.year + 1, 1, 1) - timedelta(days=1)
                else:
                    month_end = date(current.year, current.month + 1, 1) - timedelta(days=1)
                month_end = min(month_end, today)

                month_records = fetch_month(ticker, current, month_end)
                if month_records:
                    buffer.extend(month_records)
                    if len(buffer) >= BATCH_ROWS:
                        n = upsert_accounts_batch(ch, buffer)
                        total_loaded += n
                        ticker_loaded += n
                        buffer = []
                time.sleep(0.15)

                if current.month == 12:
                    current = date(current.year + 1, 1, 1)
                else:
                    current = date(current.year, current.month + 1, 1)

            if buffer:
                n = upsert_accounts_batch(ch, buffer)
                total_loaded += n
                ticker_loaded += n

            log.info(f"  {ticker}: {ticker_loaded} accounts upserted")
        except Exception as e:
            log.error(f"  {ticker} FAILED: {e}")

    log.info(f"\nDone: {total_loaded} account rows upserted to ClickHouse")


if __name__ == "__main__":
    main()
