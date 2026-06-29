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
CH_PRICES_TABLE = "prices_5min"

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
    """Insert OI records into CH moex.futoi + PG futures.futoi."""
    if not records:
        return 0

    # Group fiz/yur into one row per timestamp
    from collections import defaultdict
    groups = defaultdict(lambda: {'buy_fiz': 0, 'sell_fiz': 0, 'buy_yur': 0, 'sell_yur': 0})
    for r in records:
        key = r["time"]
        clgroup = r.get("clgroup", "")
        buy = r.get("buy_orders", 0)
        sell = r.get("sell_orders", 0)
        is_fiz = clgroup == "FIZ" or clgroup == 0 or clgroup == "0"
        is_yur = clgroup == "YUR" or clgroup == 1 or clgroup == "1"
        if is_fiz:
            groups[key]["buy_fiz"] += buy
            groups[key]["sell_fiz"] += sell
        elif is_yur:
            groups[key]["buy_yur"] += buy
            groups[key]["sell_yur"] += sell

    rows = [(ticker, bt, v["buy_fiz"], v["sell_fiz"], v["buy_yur"], v["sell_yur"])
            for bt, v in sorted(groups.items())]

    # ClickHouse moex.futoi
    client = get_ch()
    client.insert(
        "moex.futoi", rows,
        column_names=["ticker", "bt", "buy_fiz", "sell_fiz", "buy_yur", "sell_yur"],
    )
    client.close()

    # PostgreSQL futures.futoi
    try:
        pg_conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD, connect_timeout=5,
        )
        with pg_conn.cursor() as cur:
            execute_values(cur,
                """INSERT INTO futures.futoi (ticker, bt, buy_fiz, sell_fiz, buy_yur, sell_yur)
                   VALUES %s
                   ON CONFLICT (ticker, bt)
                   DO UPDATE SET buy_fiz = EXCLUDED.buy_fiz, sell_fiz = EXCLUDED.sell_fiz,
                                 buy_yur = EXCLUDED.buy_yur, sell_yur = EXCLUDED.sell_yur""",
                rows,
            )
            cur.execute("DELETE FROM futures.futoi WHERE bt < now() - INTERVAL '2 months'")
        pg_conn.commit()
        pg_conn.close()
    except Exception as e:
        log.warning("PG futoi write failed: %s", e)

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


# ── Price data (5-min candles → CH + PG) ──────────────────────────────────

def _ensure_ch_prices_table():
    """Create CH prices_5min table if not exists."""
    client = get_ch()
    client.command(f"""
        CREATE TABLE IF NOT EXISTS {CH_PRICES_TABLE} (
            ticker String,
            bt DateTime,
            opn Float64,
            hi Float64,
            lo Float64,
            prc Float64,
            vol UInt32
        ) ENGINE = ReplacingMergeTree()
        ORDER BY (ticker, bt)
    """)
    client.close()




def fetch_market_snapshot() -> dict:
    """Fetch current marketdata snapshot from MOEX ISS.
    Returns {short_ticker: {opn, hi, lo, prc, vol}} mapped by prefix."""
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.only=marketdata"
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                log.warning("HTTP %d for marketdata (attempt %d/%d)", resp.status_code, attempt + 1, RETRY_ATTEMPTS)
                time.sleep(RETRY_DELAY)
                continue
            data = resp.json()
            cols = data["marketdata"]["columns"]
            now = datetime.now()
            best = {}
            for row in data["marketdata"]["data"]:
                md = dict(zip(cols, row))
                secid = md.get("SECID", "")
                if not secid or not secid.isascii():
                    continue
                prefix = secid.rstrip("0123456789")[:-1]
                vt = int(md.get("VOLTODAY", 0) or 0)
                if prefix not in best or vt > best[prefix]["vol_today"]:
                    best[prefix] = {"vol_today": vt, "opn": float(md.get("OPEN", 0) or 0),
                                    "hi": float(md.get("HIGH", 0) or 0), "lo": float(md.get("LOW", 0) or 0),
                                    "prc": float(md.get("LAST", 0) or 0), "bt": now}
            return best
        except (requests.RequestException, json.JSONDecodeError) as e:
            log.warning("Marketdata error: %s (attempt %d/%d)", e, attempt + 1, RETRY_ATTEMPTS)
            time.sleep(RETRY_DELAY)
    return {}


def save_price_snapshot(ticker: str, data: dict, pg_write: bool = True) -> bool:
    """Write a single marketdata snapshot to CH + PG."""
    _ensure_ch_prices_table()
    client = get_ch()
    row = (ticker, data["bt"], data["opn"], data["hi"], data["lo"], data["prc"], data["vol_today"])
    try:
        client.insert("prices_5min", [row], column_names=["ticker","bt","opn","hi","lo","prc","vol"])
    except Exception as e:
        log.warning("CH insert failed: %s", e)
    client.close()
    if not pg_write:
        return True
    try:
        pg_conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, connect_timeout=5)
        with pg_conn.cursor() as cur:
            execute_values(cur, "INSERT INTO futures.prices (ticker,bt,opn,hi,lo,prc,vol) VALUES %s ON CONFLICT DO NOTHING", [row])
            cur.execute("DELETE FROM futures.prices WHERE bt < now() - INTERVAL '2 months'")
        pg_conn.commit()
        pg_conn.close()
    except Exception as e:
        log.warning("PG prices write failed: %s", e)
    return True


def load_all_prices():
    """Fetch current marketdata for ALL tickers to CH."""
    snapshot = fetch_market_snapshot()
    if not snapshot:
        log.warning("No marketdata available")
        return 0
    log.info("Loading prices for %d tickers (CH only)", len(snapshot))
    total = 0
    for ticker, data in sorted(snapshot.items()):
        try:
            save_price_snapshot(ticker, data, pg_write=False)
            total += 1
        except Exception as e:
            log.error("Failed to save %s: %s", ticker, e)
    log.info("Done: %d tickers saved to CH", total)
    return total


def load_portfolio_prices():
    """Fetch current marketdata for PORTFOLIO tickers to PG + CH."""
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, connect_timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM futures.portfolio WHERE enabled = true")
    portfolio = {r[0] for r in cur.fetchall()}
    cur.close()
    conn.close()
    snapshot = fetch_market_snapshot()
    if not snapshot:
        log.warning("No marketdata available")
        return 0
    log.info("Loading prices for portfolio (%d tickers) to PG + CH", len(portfolio))
    total = 0
    for ticker in sorted(portfolio):
        if ticker not in snapshot:
            log.info("  %s: not found in marketdata", ticker)
            continue
        data = snapshot[ticker]
        save_price_snapshot(ticker, data, pg_write=True)
        log.info("  %s: prc=%s vol=%s", ticker, data.get("prc"), data.get("vol_today"))
        total += 1
    log.info("Done: %d tickers saved to PG + CH", total)
    return total

def save_prices(ticker: str, records: list[dict], pg_write: bool = True) -> int:
    """Write 5-min bars to CH prices_5min + PG futures.prices."""
    if not records:
        return 0

    seen = set()
    rows = []
    for r in records:
        key = r["bt"]
        if key not in seen:
            seen.add(key)
            rows.append((ticker, r["bt"], r["opn"], r["hi"], r["lo"], r["prc"], r["vol"]))

    # ClickHouse
    _ensure_ch_prices_table()
    client = get_ch()
    client.insert(
        CH_PRICES_TABLE,
        rows,
        column_names=["ticker", "bt", "opn", "hi", "lo", "prc", "vol"],
    )
    client.close()

    # PostgreSQL (только для портфеля)
    if not pg_write:
        return len(rows)

    try:
        pg_conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                                   user=DB_USER, password=DB_PASSWORD, connect_timeout=5)
        with pg_conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO futures.prices (ticker, bt, opn, hi, lo, prc, vol)
                   VALUES %s
                   ON CONFLICT (ticker, bt) DO NOTHING""",
                rows,
            )
            cur.execute("DELETE FROM futures.prices WHERE bt < now() - INTERVAL '2 months'")
        pg_conn.commit()
        pg_conn.close()
    except Exception as e:
        log.warning("PG prices write failed: %s", e)

    return len(rows)


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MOEX data loader")
    parser.add_argument("--dry-run", action="store_true", help="Test imports only")
    parser.add_argument("--load-prices", action="store_true", help="Load 5-min price data for ALL tickers (CH only)")
    parser.add_argument("--load-portfolio-prices", action="store_true", help="Load 5-min price data for PORTFOLIO tickers (PG + CH)")
    args = parser.parse_args()

    if args.dry_run:
        log.info("dry-run: imports OK")
        sys.exit(0)

    if args.load_portfolio_prices:
        load_portfolio_prices()
    elif args.load_prices:
        load_all_prices()
    else:
        update_all()
