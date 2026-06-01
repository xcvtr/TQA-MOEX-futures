#!/usr/bin/env python3
"""
MOEX Open Interest Loader

Fetches OI data from MOEX ISS API and stores in PostgreSQL.
Based on the Excavator MQL5 EA module (DataProviders/MOEX_OI.h).

API: https://iss.moex.com/iss/analyticalproducts/futoi/securities/{ticker}.csv
     ?iss.meta=off&iss.only=futoi&from={date}&till={date}&latest=0

Columns returned: sess_id, seqnum, tradedate, tradetime, ticker,
                   clgroup (FIZ/YUR), pos_long, pos_short,
                   pos_long_num, pos_short_num, systime
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

# Add project to path
sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

# Try loading .env
env_path = Path.home() / ".hermes" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    MOEX_OI_TICKERS, START_DATE, DAYS_BACKFILL,
    REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY,
    MOEX_LOGIN, MOEX_PASSWORD,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("moex_oi")


# ── Database ──────────────────────────────────────────────────────────────

def get_db():
    """Connect to the moex database."""
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    conn.autocommit = False
    return conn


def get_last_date(conn, ticker: str) -> Optional[date]:
    """Get the last date we have data for this ticker."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(time)::date FROM openinterest_moex WHERE symbol = %s",
            (ticker,)
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def save_oi_records(conn, ticker: str, records: list[dict]) -> int:
    """Insert OI records into DB. Returns count of inserted rows."""
    if not records:
        return 0

    rows = []
    seen = set()
    for r in records:
        key = (ticker, r["time"], r["clgroup"])
        if key not in seen:
            seen.add(key)
            rows.append((
                ticker,
                r["time"],
                r["buy_orders"],
                r["sell_orders"],
                r["clgroup"],
            ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO openinterest_moex
               (symbol, time, buy_orders, sell_orders, clgroup)
               VALUES %s
               ON CONFLICT (symbol, time, clgroup)
               DO UPDATE SET buy_orders = EXCLUDED.buy_orders,
                             sell_orders = EXCLUDED.sell_orders""",
            rows,
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


# ── MOEX ISS API ──────────────────────────────────────────────────────────

_moex_cookie: Optional[str] = None
_moex_cookie_ts: float = 0


def moex_auth() -> Optional[str]:
    """
    Authenticate on passport.moex.com and return cookie header.
    Returns None if no credentials or auth fails.
    """
    global _moex_cookie, _moex_cookie_ts

    if not MOEX_LOGIN or not MOEX_PASSWORD:
        return None

    # Reuse cached cookie (re-auth every hour)
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

        # Extract _passport_session cookie from Set-Cookie header
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


def fetch_oi_day(ticker: str, day: date, cookie: Optional[str] = None) -> Optional[list[dict]]:
    """
    Fetch one day of OI data for a ticker from MOEX ISS API.
    Returns list of records or None if no data.
    """
    url = (
        f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
        f"{ticker}.csv"
        f"?iss.meta=off&iss.only=futoi"
        f"&from={day.isoformat()}&till={day.isoformat()}&latest=0"
    )

    for attempt in range(RETRY_ATTEMPTS):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
            if cookie:
                c = cookie.replace("Cookie: ", "").replace("\r\n", "").strip()
                headers["Cookie"] = c

            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                log.warning("HTTP %d for %s on %s (attempt %d/%d)",
                            resp.status_code, ticker, day,
                            attempt + 1, RETRY_ATTEMPTS)
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
                continue

            text = resp.text

            # "Invalid date" = no data for this day
            if "Invalid date" in text or "No data" in text:
                return None

            # Parse CSV (semicolon-delimited, MOEX ISS format)
            reader = csv.reader(io.StringIO(text), delimiter=";")
            records = []
            for row in reader:
                if len(row) < 11:
                    continue
                # Skip section header and empty lines
                if row[0].strip() in ("futoi", "") or row[0] == "sess_id":
                    continue

                clgroup_raw = row[5].strip().upper()
                if clgroup_raw not in ("FIZ", "YUR"):
                    continue

                try:
                    tradedate_str = row[2].strip()
                    tradetime_str = row[3].strip()
                    dt_str = f"{tradedate_str} {tradetime_str}"
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
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
            log.warning("Network error for %s on %s: %s (attempt %d/%d)",
                        ticker, day, e, attempt + 1, RETRY_ATTEMPTS)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)

    return None


# ── Main logic ────────────────────────────────────────────────────────────

def update_ticker(conn, ticker: str, days_back: int = DAYS_BACKFILL,
                  cookie: Optional[str] = None) -> tuple[int, int]:
    """
    Update OI data for a single ticker.
    Returns (days_checked, records_inserted).
    """
    last_date = get_last_date(conn, ticker)
    if last_date:
        start = last_date
    else:
        start = datetime.strptime(START_DATE, "%Y-%m-%d").date()

    end = date.today()
    days_range = (end - start).days

    # If we have recent data, only check last N days
    if days_range > days_back:
        start = end - timedelta(days=days_back)

    checked = 0
    inserted = 0
    current = start

    while current <= end:
        # Skip weekends (MOEX is closed)
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        records = fetch_oi_day(ticker, current, cookie=cookie)
        checked += 1

        if records:
            n = save_oi_records(conn, ticker, records)
            inserted += n
            if n > 0:
                log.info("  %s %s: %d records (%d new)",
                         ticker, current, len(records), n)

        current += timedelta(days=1)

    return checked, inserted


def update_all(days_back: int = DAYS_BACKFILL):
    """Update OI data for all configured tickers."""
    conn = get_db()
    total_checked = 0
    total_inserted = 0

    log.info("=== MOEX OI Loader ===")
    log.info("Tickers: %d, lookback: %d days", len(MOEX_OI_TICKERS), days_back)

    # Authenticate (if credentials set)
    cookie = moex_auth()
    if cookie:
        log.info("Using authenticated session (full data access)")
    else:
        log.info("No MOEX credentials — data limited to 14+ days old")

    for ticker in MOEX_OI_TICKERS:
        try:
            c, ins = update_ticker(conn, ticker, days_back, cookie=cookie)
            total_checked += c
            total_inserted += ins
            log.info("  %s: checked %d days, inserted %d records",
                     ticker, c, ins)
        except Exception as e:
            log.error("Failed to update %s: %s", ticker, e)
            conn.rollback()

    conn.close()
    log.info("=== Done: %d tickers, %d days checked, %d records inserted ===",
             len(MOEX_OI_TICKERS), total_checked, total_inserted)
    return total_inserted


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DAYS_BACKFILL
    update_all(days_back=days)
