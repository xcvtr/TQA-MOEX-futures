#!/usr/bin/env python3
"""
MOEX Price Snapshot Loader

Fetches current marketdata for all futures from ISS API
and saves prices (open, high, low, last, volume, OI) to ClickHouse.

API: https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.only=marketdata
"""

import sys, os, json, time
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import MOEX_OI_TICKERS, CH_HOST, CH_PORT, CH_DB

import requests
import clickhouse_connect

TICKER_SET = set(MOEX_OI_TICKERS)
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def save_prices(ch, records: list[tuple]) -> int:
    """Save price records to CH. Returns count."""
    if not records:
        return 0

    ch.insert(
        "moex.prices",
        records,
        column_names=["symbol", "time", "open", "high", "low",
                       "last", "volume", "open_interest", "settle_price"],
    )
    return len(records)


def fetch_snapshot() -> list[tuple]:
    """
    Fetch current snapshot from MOEX ISS.
    Returns list of tuples: (symbol, time, open, high, low, last, volume, OI, settle_price)
    """
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.only=marketdata"
    headers = {"User-Agent": "Mozilla/5.0"}

    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                log.warning("HTTP %d (attempt %d/%d)",
                            resp.status_code, attempt + 1, RETRY_ATTEMPTS)
                time.sleep(RETRY_DELAY)
                continue

            data = resp.json()
            cols = data["marketdata"]["columns"]  # list of strings
            now = datetime.now()

            records = []
            for row in data["marketdata"]["data"]:
                md = dict(zip(cols, row))
                secid = md.get("SECID", "")
                # Skip non-interesting and option-like codes
                if secid in ("", None) or not secid.isascii():
                    continue
                if secid not in TICKER_SET:
                    continue

                try:
                    rec = (
                        secid,
                        now,
                        float(md.get("OPEN", 0) or 0),
                        float(md.get("HIGH", 0) or 0),
                        float(md.get("LOW", 0) or 0),
                        float(md.get("LAST", 0) or 0),
                        int(md.get("VOLTODAY", 0) or 0),
                        int(md.get("OPENPOSITION", 0) or 0),
                        float(md.get("SETTLEPRICE", 0) or 0),
                    )
                    records.append(rec)
                except (ValueError, TypeError):
                    continue

            return records

        except (requests.RequestException, json.JSONDecodeError) as e:
            log.warning("Fetch error (attempt %d/%d): %s",
                        attempt + 1, RETRY_ATTEMPTS, e)
            time.sleep(RETRY_DELAY)

    return []


def update_snapshot():
    """Fetch and save current snapshot."""
    ch = get_ch()
    records = fetch_snapshot()
    if not records:
        log.warning("No records fetched")
        return 0

    count = save_prices(ch, records)
    log.info("Saved %d price snapshots to ClickHouse", count)
    return count


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("moex_price")
    update_snapshot()
