#!/usr/bin/env python3
"""
Bulk OI History Loader — загружает ВСЮ доступную историю futoi с 2021.

Читает все поля: pos_long, pos_short, pos_long_num, pos_short_num.
Пропускает уже загруженные даты (upsert).
"""
import sys, os, csv, io, time
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    MOEX_OI_TICKERS, REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY,
    MOEX_LOGIN, MOEX_PASSWORD,
)

import requests
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("bulk_oi")

# ── Настройки ──────────────────────────────────────────────────────────
HISTORY_START = date(2021, 1, 1)
BATCH_ROWS = 5000  # rows per INSERT
MAX_RECORDS_PER_DAY = 100  # max records per ticker per day (safety)

# ── Database ────────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    conn.autocommit = False
    return conn


def get_existing_dates(conn, ticker: str) -> set:
    """Get set of (date, clgroup) that have COMPLETE data (incl. new columns)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT time::date, clgroup FROM openinterest_moex "
        "WHERE symbol = %s AND buy_accounts > 0 AND sell_accounts > 0",
        (ticker,)
    )
    result = {(r[0], r[1]) for r in cur.fetchall()}
    cur.close()
    return result


def save_batch(conn, records: list) -> int:
    """Bulk upsert with all 6 value fields."""
    if not records:
        return 0
    rows = []
    seen = set()
    for r in records:
        key = (r["symbol"], r["time"], r["clgroup"])
        if key not in seen:
            seen.add(key)
            rows.append((
                r["symbol"], r["time"],
                r["buy_orders"], r["sell_orders"],
                r["buy_accounts"], r["sell_accounts"],
                r["clgroup"],
            ))
    if not rows:
        return 0

    with conn.cursor() as cur:
        execute_values(cur,
            """INSERT INTO openinterest_moex
               (symbol, time, buy_orders, sell_orders, buy_accounts, sell_accounts, clgroup)
               VALUES %s
               ON CONFLICT (symbol, time, clgroup)
               DO UPDATE SET buy_orders = EXCLUDED.buy_orders,
                             sell_orders = EXCLUDED.sell_orders,
                             buy_accounts = EXCLUDED.buy_accounts,
                             sell_accounts = EXCLUDED.sell_accounts""",
            rows,
        )
        n = cur.rowcount
    conn.commit()
    return n


# ── MOEX futoi fetch ───────────────────────────────────────────────────

def fetch_day(ticker: str, day: date) -> list[dict]:
    """Fetch one day of OI data for a ticker. Returns all rows with ALL columns."""
    url = (
        f"https://iss.moex.com/iss/analyticalproducts/futoi/securities/"
        f"{ticker}.csv"
        f"?iss.meta=off&iss.only=futoi"
        f"&from={day.isoformat()}&till={day.isoformat()}&latest=0"
    )

    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                log.warning("  HTTP %d for %s on %s (attempt %d/%d)", resp.status_code, ticker, day, attempt+1, RETRY_ATTEMPTS)
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
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
                    buy_orders = int(row[7]) if row[7].strip() else 0
                    sell_orders = abs(int(row[8])) if row[8].strip() else 0
                    buy_accounts = int(row[9]) if len(row) > 9 and row[9].strip() else 0
                    sell_accounts = int(row[10]) if len(row) > 10 and row[10].strip() else 0
                except (ValueError, IndexError):
                    continue

                records.append({
                    "symbol": ticker,
                    "time": dt,
                    "buy_orders": buy_orders,
                    "sell_orders": sell_orders,
                    "buy_accounts": buy_accounts,
                    "sell_accounts": sell_accounts,
                    "clgroup": 0 if clgroup_raw == "FIZ" else 1,
                })

            return records

        except requests.RequestException as e:
            log.warning("  Network error for %s on %s: %s (attempt %d/%d)", ticker, day, e, attempt+1, RETRY_ATTEMPTS)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)

    return []


# ── Main ────────────────────────────────────────────────────────────────

def load_ticker_history(conn, ticker: str) -> tuple[int, int, int]:
    """Load all available history for one ticker. Returns (days_checked, records_loaded, days_skipped)."""
    existing = get_existing_dates(conn, ticker)
    today = date.today()

    checked = 0
    loaded = 0
    skipped = 0
    current = HISTORY_START
    buffer = []

    while current <= today:
        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        # Check if this day is already fully loaded (both FIZ and YUR)
        has_fiz = (current, 0) in existing
        has_yur = (current, 1) in existing
        if has_fiz and has_yur:
            skipped += 1
            current += timedelta(days=1)
            continue

        records = fetch_day(ticker, current)
        checked += 1

        if records:
            # Mark this date as done for future reference
            for r in records:
                buffer.append(r)
            existing.add((current, 0))
            existing.add((current, 1))

            if len(buffer) >= BATCH_ROWS:
                n = save_batch(conn, buffer)
                loaded += n
                buffer = []

        current += timedelta(days=1)

    # Flush remaining
    if buffer:
        n = save_batch(conn, buffer)
        loaded += n

    return checked, loaded, skipped


def main():
    log.info("=" * 60)
    log.info("BULK OI HISTORY LOADER")
    log.info(f"Tickers: {len(MOEX_OI_TICKERS)}, from: {HISTORY_START}, to: {date.today()}")
    log.info("=" * 60)

    conn = get_db()
    total_checked = 0
    total_loaded = 0
    total_skipped = 0

    for ticker in sorted(MOEX_OI_TICKERS):
        log.info(f"\n--- {ticker} ---")
        try:
            c, l, s = load_ticker_history(conn, ticker)
            total_checked += c
            total_loaded += l
            total_skipped += s
            log.info(f"  {ticker}: checked={c}d, loaded={l} records, skipped={s}d (already had)")
        except Exception as e:
            log.error(f"  {ticker} FAILED: {e}")
            conn.rollback()
        # Polite delay
        time.sleep(0.05)

    conn.close()
    log.info(f"\n{'='*60}")
    log.info(f"DONE: {total_checked} days checked, {total_loaded} records loaded, {total_skipped} days skipped")
    log.info(f"{'='*60}")
    return total_loaded


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("bulk_oi")
    main()
