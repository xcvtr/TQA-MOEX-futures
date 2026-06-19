#!/usr/bin/env python3
"""
MOEX Open Interest Loader — ClickHouse + PostgreSQL dual write.

Fetches OI data from MOEX ISS API and stores in ClickHouse moex.openinterest
and PostgreSQL openinterest_moex (primary 10.0.0.63).

URL trick: MOEX allows access to fresh OI data when from/till dates are >14 days old.
Even though it ignores the actual dates and returns the latest available data,
the date range must be outside the 14-day window to bypass the free-user restriction.
The trailing 'd' on till is also required as a secondary bypass.

API: https://iss.moex.com/iss/analyticalproducts/futoi/securities/{ticker}.csv
     ?iss.meta=off&iss.only=futoi&from={old_date}&till={old_date}d
"""

import csv
import io
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, date
from typing import Optional

import requests
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))
from config import (MOEX_OI_TICKERS, START_DATE, DAYS_BACKFILL,
                    REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY,
                    MOEX_LOGIN, MOEX_PASSWORD, DB_HOST, DB_PORT, DB_NAME,
                    DB_USER, DB_PASSWORD)

# Override DB_HOST for writes — point to PG primary (10.0.0.63)
PG_HOST = os.getenv("MOEX_PG_HOST", "127.0.0.1")
PG_PORT = int(os.getenv("MOEX_PG_PORT", "5432"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("moex_oi")

try:
    import clickhouse_connect
except ImportError:
    log.error("clickhouse-connect not installed. Run: pip install clickhouse-connect")
    sys.exit(1)

# ── ClickHouse connection ───────────────────────────────────────────────────

CH_HOST = os.getenv("CH_HOST", "10.0.0.64")
CH_PORT = int(os.getenv("CH_PORT", "8123"))
CH_DB = "moex"
CH_TABLE = "openinterest"

# For the free-tier bypass: use dates that are definitely >14 days old
# MOEX ignores them and returns the latest data anyway
BYPASS_FROM = "2020-01-03"
BYPASS_TILL = "2020-01-10"


def get_ch() -> 'clickhouse_connect.driver.Client':
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def get_last_time(ticker: str) -> Optional[datetime]:
    """Get the last snapshot time for this ticker in CH."""
    client = get_ch()
    row = client.query(
        f"SELECT max(time) FROM {CH_TABLE} WHERE symbol = {{sym:String}}",
        parameters={"sym": ticker},
    ).result_rows
    client.close()
    if row and row[0][0]:
        return row[0][0]
    return None


def save_oi_records(ticker: str, records: list[dict]) -> int:
    """Insert OI records into ClickHouse AND PostgreSQL. Returns count of rows inserted."""
    if not records:
        return 0

    seen = set()
    rows = []
    now = datetime.now()
    for r in records:
        key = (r["time"], r["clgroup"])
        if key not in seen:
            seen.add(key)
            rows.append((
                r["time"], ticker, r["buy_orders"], r["sell_orders"],
                r["clgroup"], now, 0, 0,
            ))

    # ClickHouse
    client = get_ch()
    client.insert(
        CH_TABLE,
        rows,
        column_names=["time", "symbol", "buy_orders", "sell_orders",
                      "clgroup", "created_at", "buy_accounts", "sell_accounts"],
    )
    client.close()

    # PostgreSQL (primary 10.0.0.63, configured via DB_HOST/DB_PORT/DB_NAME)
    pg_rows = [(ticker, r["time"], r["buy_orders"], r["sell_orders"], r["clgroup"])
               for r in records]
    try:
        pg_conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with pg_conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO openinterest_moex (symbol, time, buy_orders, sell_orders, clgroup)
                   VALUES %s
                   ON CONFLICT (symbol, time, clgroup)
                   DO UPDATE SET buy_orders = EXCLUDED.buy_orders,
                                 sell_orders = EXCLUDED.sell_orders,
                                 created_at = NOW()""",
                [(ticker, r["time"], r["buy_orders"], r["sell_orders"], r["clgroup"])
                 for r in records],
            )
        pg_conn.commit()
        pg_conn.close()
    except Exception as e:
        log.warning("PG write failed (standby?): %s", e)

    return len(rows)


# ── MOEX ISS API ──────────────────────────────────────────────────────────

_moex_cookie: Optional[str] = None
_moex_cookie_ts: float = 0


def moex_auth() -> Optional[str]:
    global _moex_cookie, _moex_cookie_ts
    if not MOEX_LOGIN or not MOEX_PASSWORD:
        return None
    if _moex_cookie and time.time() - _moex_cookie_ts < 3600:
        return _moex_cookie

    url = "https://passport.moex.com/login"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = f"user[credentials]={MOEX_LOGIN}&user[password]={MOEX_PASSWORD}"

    try:
        resp = requests.post(url, headers=headers, data=data,
                             timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            log.warning("MOEX auth HTTP %d", resp.status_code)
            return None
        set_cookie = resp.headers.get("Set-Cookie", "")
        m = re.search(r'_passport_session=[^;]+', set_cookie)
        if not m:
            log.warning("MOEX auth: _passport_session cookie not found")
            return None
        _moex_cookie = f"Cookie: {m.group()}; \r\n"
        _moex_cookie_ts = time.time()
        log.info("MOEX auth successful")
        return _moex_cookie
    except requests.RequestException as e:
        log.warning("MOEX auth failed: %s", e)
        return None


def fetch_oi_snapshot(ticker: str) -> Optional[list[dict]]:
    """
    Fetch OI data for a ticker from MOEX ISS API.

    Uses deliberately old dates (>14 days) + trailing 'd' to bypass the
    free-user restriction. MOEX ignores the actual date range and returns
    the latest available snapshots.
    """
    url = (
        f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
        f"{ticker}.csv"
        f"?iss.meta=off&iss.only=futoi"
        f"&from={BYPASS_FROM}&till={BYPASS_TILL}d"
    )

    for attempt in range(RETRY_ATTEMPTS):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                log.warning("HTTP %d for %s (attempt %d/%d)",
                            resp.status_code, ticker, attempt + 1, RETRY_ATTEMPTS)
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
                continue

            text = resp.text
            if "Invalid date" in text or "No data" in text:
                log.warning("MOEX rejected %s (free user restriction)", ticker)
                return None

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
                    dt = datetime.strptime(
                        f"{row[2].strip()} {row[3].strip()}", "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue

                try:
                    buy_orders = int(row[7]) if row[7].strip() else 0
                    sell_orders = abs(int(row[8])) if row[8].strip() else 0
                except (ValueError, IndexError):
                    continue

                records.append({
                    "time": dt,
                    "buy_orders": buy_orders,
                    "sell_orders": sell_orders,
                    "clgroup": 0 if clgroup_raw == "FIZ" else 1,
                })

            return records if records else None

        except requests.RequestException as e:
            log.warning("Network error for %s: %s (attempt %d/%d)",
                        ticker, e, attempt + 1, RETRY_ATTEMPTS)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)

    return None


# ── Main logic ────────────────────────────────────────────────────────────

def update_all():
    """Fetch latest OI snapshots for all tickers into ClickHouse + PostgreSQL."""
    total_new = 0
    total_skipped = 0

    log.info("=== MOEX OI Loader -> ClickHouse + PostgreSQL ===")
    log.info("Tickers: %d", len(MOEX_OI_TICKERS))
    log.info("Bypass dates: %s .. %s", BYPASS_FROM, BYPASS_TILL)

    for ticker in MOEX_OI_TICKERS:
        try:
            records = fetch_oi_snapshot(ticker)
            if not records:
                log.info("  %s: no data", ticker)
                total_skipped += 1
                continue

            n = save_oi_records(ticker, records)
            log.info("  %s: %d records (new: %d)", ticker, len(records), n)
            if n > 0:
                total_new += n

        except Exception as e:
            log.error("Failed to update %s: %s", ticker, e)

    log.info("=== Done: %d/%d tickers updated, %d new rows ===",
             len(MOEX_OI_TICKERS) - total_skipped, len(MOEX_OI_TICKERS), total_new)
    return total_new


if __name__ == "__main__":
    update_all()
