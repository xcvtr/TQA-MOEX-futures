#!/usr/bin/env python3
"""
MOEX 5-Minute Price History Loader v2
Fetches 5-minute candles for all MOEX futures via Alor OpenAPI V2.
Generates historical contract names for expired contracts.

Usage:
    python3 price_history_5m.py                    # default load (18 high-liquidity tickers)
    python3 price_history_5m.py Si                 # single asset (any ticker)
    python3 price_history_5m.py Si BR ED           # multiple assets

Env:
    ALOR_JWT - JWT token for Alor API auth
"""

import sys, os, json, time
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, MOEX_OI_TICKERS

import requests
import psycopg2
from psycopg2.extras import execute_values

# ── Config ────────────────────────────────────────────────────────────────────
ALOR_BASE = "https://api.alor.ru"
ALOR_JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
ALOR_HEADERS = {"Authorization": f"Bearer {ALOR_JWT}"}
ALOR_TF = 300  # 5 min
BATCH_SIZE = 10000
DATA_START = date(2023, 1, 1)
QUARTER_MONTHS = [3, 6, 9, 12]  # March, June, September, December

# Tickers with monthly expiry (e.g., Brent)
MONTHLY_TICKERS = {"BR"}

# Short ticker → ASSETCODE mapping
TICKER_TO_ASSET = {
    "Si": "Si", "BR": "BR", "ED": "ED", "Eu": "Eu",
    "GD": "GOLD", "GZ": "GAZR", "LK": "LKOH", "SR": "SBRF", "VB": "VTBR",
    "RN": "ROSN", "AF": "AFLT", "AL": "ALRS", "SN": "SNGR", "SP": "SPBE",
    "TT": "T", "HY": "HYDR", "NM": "NLMK", "MG": "MAGN", "MM": "MOEX",
    "ME": "MTLR", "VI": "RVI", "SV": "SILV", "NG": "NG",
    "PD": "PLD", "PT": "PLT", "RI": "RTS",
    "CNYRUBF": "CNYRUBF", "USDRUBF": "USDRUBF", "EURRUBF": "EURRUBF",
    "GLDRUBF": "GLDRUBF", "IMOEXF": "IMOEXF", "GAZPF": "GAZPF",
    "SBERF": "SBERF", "GL": "GL", "X5": "X5", "YD": "YDEX",
    "BM": "BRM", "CC": "COCOA", "CE": "COPPER", "CH": "CHINA",
    "DX": "DAX", "FF": "FNI", "GK": "GMKN", "HS": "HANG",
    "IB": "IBIT", "KC": "COFFEE", "MC": "MXI", "MY": "MY",
    "NA": "NASD", "NR": "NICKEL", "OJ": "ORANGE", "RB": "RUBBER",
    "RL": "RURAL", "RM": "RTSM", "SE": "SGZH", "SS": "SMLT",
    "W4": "WHEAT", "UC": "UCNY", "AU": "AUDU",
    "TN": "T", "SF": "SFIN",
    "CR": "CR", "MN": "MN",
    "MX": "MXI",
}
ASSET_TO_TICKER = {v: k for k, v in TICKER_TO_ASSET.items()}

# Tickers that use direct Alor symbol (not quarterly contract names)
DIRECT_SYMBOLS = {"CNYRUBF", "EURRUBF", "GLDRUBF", "USDRUBF", "SBERF", "GAZPF", "IMOEXF"}

# Tickers that exist in MOEX OI (futoi) but have NO tradable contracts on Alor/MOEX futures
# These are only loaded into openinterest_moex, not moex_prices_5m
OI_ONLY_TICKERS = {"CR", "MN", "MY", "RB", "RL"}

# Tickers with extremely low liquidity (<60 real candles/day on front-month)
# Loading them produces mostly noise — excluded from 5m table
LOW_LIQUIDITY_TICKERS = {"CH", "VI", "AU", "FF"}

# Core liquid tickers (≥178 real candles/day) — default full load
# Includes all KEEP tickers from Volume Surge + Divergence scan
HIGH_LIQUIDITY_TICKERS = {
    "CNYRUBF", "CC", "Si", "BR", "NG", "IMOEXF", "BM", "VB",
    "SV", "NA", "USDRUBF", "MC", "GD",
    "GLDRUBF", "SR", "SS", "GZ", "GL",
    # KEEP tickers (Volume Surge + Divergence)
    "AF", "AL", "CE", "DX", "HS", "HY", "MG", "NM", "NR",
    "OJ", "PD", "SE", "SF", "SN", "SP", "TN", "TT", "W4", "YD",
}


def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )
    conn.autocommit = False
    return conn


def get_current_contracts() -> dict[str, list]:
    """Get currently listed contracts from MOEX ISS, grouped by asset."""
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.meta=off&iss.only=securities"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    data = resp.json()
    cols = data["securities"]["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    contracts = defaultdict(list)
    for row in data["securities"]["data"]:
        asset = row[idx["ASSETCODE"]] or ""
        if asset not in ASSET_TO_TICKER:
            continue
        secid = row[idx["SECID"]]
        shortname = row[idx["SHORTNAME"]]
        last_trade = row[idx["LASTTRADEDATE"]] or ""
        prev_oi = row[idx["PREVOPENPOSITION"]] or 0
        contracts[asset].append({
            "secid": secid,
            "shortname": shortname,
            "alor_symbol": shortname.upper(),
            "last_trade": last_trade,
            "open_interest": int(prev_oi),
        })
    return contracts


def generate_historical_contracts(asset_code: str, earliest_listed: str,
                                    existing_symbols: set[str] | None = None,
                                    monthly: bool = False) -> list[dict]:
    """
    Generate historical quarterly or monthly contract names from DATA_START up to the earliest listed one.
    Skips contracts that already exist in the current list.
    monthly=True: use all 12 months (for Brent and other monthly futures).
    """
    if existing_symbols is None:
        existing_symbols = set()

    if not earliest_listed:
        return []

    try:
        earliest_dt = datetime.strptime(earliest_listed.split("T")[0] if "T" in earliest_listed else earliest_listed, "%Y-%m-%d")
    except ValueError:
        return []

    contracts = []
    year = DATA_START.year
    month = DATA_START.month

    if monthly:
        # All 12 months (for BR Brent)
        cycle = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    else:
        cycle = QUARTER_MONTHS

    while year < earliest_dt.year or (year == earliest_dt.year and month < earliest_dt.month):
        qm = None
        for m in cycle:
            if m > month:
                qm = m
                break
        if qm is None:
            year += 1
            qm = cycle[0]
        month = qm

        exp_day = 15
        try:
            exp_date = date(year, month, exp_day)
        except ValueError:
            exp_date = date(year, month, 28)

        if exp_date >= earliest_dt.date():
            break

        alor_sym = f"{asset_code.upper()}-{month}.{str(year)[-2:]}"

        # Skip if already in current contracts
        if alor_sym in existing_symbols:
            continue

        contracts.append({
            "secid": f"GEN_{alor_sym}",
            "shortname": f"{asset_code}-{month}.{year}",
            "alor_symbol": alor_sym,
            "last_trade": exp_date.strftime("%Y-%m-%d"),
            "open_interest": 0,
            "historical": True,
        })

    return contracts


def fetch_candles(symbol: str, from_ts: int, to_ts: int) -> list[dict]:
    """Fetch candles from Alor API with pagination."""
    all_candles = []
    params = {
        "exchange": "MOEX",
        "symbol": symbol,
        "tf": ALOR_TF,
        "from": from_ts,
        "to": to_ts,
    }

    while True:
        try:
            resp = requests.get(
                f"{ALOR_BASE}/md/v2/history",
                headers=ALOR_HEADERS,
                params=params,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                candles = data.get("history", [])
                all_candles.extend(candles)
                next_ts = data.get("next")
                if next_ts and next_ts < to_ts:
                    params["from"] = next_ts
                    time.sleep(0.02)
                    continue
                return all_candles
            elif resp.status_code == 404:
                return []
            else:
                time.sleep(1)
        except Exception:
            time.sleep(1)
            break

    return all_candles


def save_batch(conn, records: list) -> int:
    """Upsert batch of (symbol, time, open, high, low, close, volume, contract)."""
    if not records:
        return 0
    with conn.cursor() as cur:
        execute_values(cur,
            """INSERT INTO moex_prices_5m (symbol, time, open, high, low, close, volume, contract)
               VALUES %s
               ON CONFLICT (symbol, time) DO UPDATE SET
                   open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                   close=EXCLUDED.close, volume=EXCLUDED.volume,
                   contract=EXCLUDED.contract, updated_at=NOW()""",
            records)
        n = cur.rowcount
    conn.commit()
    return n


def to_unixts(d: date, end_of_day: bool = False) -> int:
    """Convert date to Unix timestamp."""
    if end_of_day:
        dt = datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)
    else:
        dt = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    return int(dt.timestamp())


def main():
    tickers_to_load = sys.argv[1:] if len(sys.argv) > 1 else sorted(HIGH_LIQUIDITY_TICKERS)

    print(f"=== MOEX 5m Price [{datetime.now():%Y-%m-%d %H:%M:%S}] "
          f"({len(tickers_to_load)} tickers) ===")

    conn = get_db()
    contracts = get_current_contracts()
    total = 0
    today = date.today()

    for ticker in sorted(tickers_to_load):
        asset = TICKER_TO_ASSET.get(ticker)
        if not asset:
            print(f"\n{ticker}: unknown asset, skip")
            continue

        clist = list(contracts.get(asset, []))

        # Generate historical contracts if needed
        existing_symbols = {c["alor_symbol"] for c in clist}

        earliest = min((c["last_trade"] for c in clist), default=None)
        hist_contracts = []
        if earliest and DATA_START < datetime.strptime(earliest.split("T")[0], "%Y-%m-%d").date():
            monthly = ticker in MONTHLY_TICKERS
            hist_contracts = generate_historical_contracts(asset, earliest, existing_symbols, monthly=monthly)

        # Sort contracts: highest OI first (most liquid = de facto front-month),
        # then by expiry for historicals
        all_c_list = sorted(hist_contracts + clist,
                            key=lambda x: (-x.get("open_interest", 0), x.get("last_trade", "")))

        print(f"\n{ticker:10s} ({asset}): {len(all_c_list)} contracts "
              f"({len(hist_contracts)} historical, {len(clist)} current)")

        # ── Direct symbol mode ──────────────────────────────────────────────
        if ticker in DIRECT_SYMBOLS or not all_c_list:
            alor_sym = ticker
            print(f"  (direct Alor symbol: {alor_sym})")

            last_ts = None
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(time) FROM moex_prices_5m WHERE symbol = %s", (ticker,))
                row = cur.fetchone()
                if row and row[0]:
                    last_ts = int(row[0].replace(tzinfo=timezone.utc).timestamp())

            from_ts = to_unixts(DATA_START)
            to_ts = int(datetime.now(tz=timezone.utc).timestamp())
            if last_ts and last_ts > from_ts:
                from_ts = last_ts
            if from_ts >= to_ts:
                print(f"  → up to date")
                continue

            candles = fetch_candles(alor_sym, from_ts, to_ts)
            print(f"  {alor_sym:18s}: {len(candles):>6d} candles")
            if candles:
                records = []
                for c in candles:
                    ts = datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None)
                    records.append((ticker, ts, c["open"], c["high"], c["low"],
                                    c["close"], int(c.get("volume", 0)), alor_sym))
                records.sort(key=lambda r: r[1])
                n = save_batch(conn, records)
                print(f"  → {len(records)} records saved ({n} upserted)")
            total += len(candles)
            continue

        # ── Contract mode ───────────────────────────────────────────────────

        # Get last timestamp in DB for this ticker
        last_ts = None
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(time) FROM moex_prices_5m WHERE symbol = %s", (ticker,))
            row = cur.fetchone()
            if row and row[0]:
                last_ts = int(row[0].replace(tzinfo=timezone.utc).timestamp())

        seen = {}  # (symbol, time) → record (front-month contract wins)
        ticker_total = 0

        for c in all_c_list:
            alor_sym = c["alor_symbol"]
            secid = c.get("secid", "")
            last_trade = c.get("last_trade", "")
            oi = c.get("open_interest", 0)

            if not last_trade or not alor_sym:
                continue

            try:
                c_end = datetime.strptime(last_trade.split("T")[0], "%Y-%m-%d").date()
            except ValueError:
                c_end = today

            # Each contract actively trades for ~4 months, ending at expiry
            c_start = c_end - timedelta(days=120)
            if c_start < DATA_START:
                c_start = DATA_START

            from_ts = to_unixts(c_start)
            to_ts = to_unixts(c_end, end_of_day=True)

            # Skip if entirely loaded
            if last_ts and from_ts > last_ts:
                continue
            if last_ts and to_ts < last_ts:
                from_ts = max(from_ts, last_ts)

            if from_ts >= to_ts:
                continue

            candles = fetch_candles(alor_sym, from_ts, to_ts)
            if not candles:
                print(f"  {alor_sym:18s}: 0 candles")
                continue

            n_new = 0
            for c in candles:
                key = (ticker,
                       datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None))
                vol = int(c["volume"]) if c.get("volume") else 0
                rec = (ticker, key[1], c["open"], c["high"], c["low"], c["close"],
                       vol, secid)
                if key not in seen:
                    seen[key] = rec
                    n_new += 1

            ticker_total += len(candles)
            print(f"  {alor_sym:18s}: {len(candles):>6d} candles  OI={oi:>9,}  "
                  f"({c_start} .. {c_end})")

        # Save all deduplicated records sorted by time
        if seen:
            deduped = sorted(seen.values(), key=lambda r: r[1])
            n = save_batch(conn, deduped)
            print(f"  → {len(seen)} unique records saved ({n} upserted)")
        else:
            print(f"  → 0 records")

        total += ticker_total

    print(f"\n=== Done: {total} candles ===")
    conn.close()


if __name__ == "__main__":
    main()
