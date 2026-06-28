#!/usr/bin/env python3
"""
SQLite-to-PostgreSQL for large DOM datasets (Windows → PG).
Reads SQLite locally via rowid seek, writes via execute_values batches.
More reliable than copy_expert over network.

Usage (on Windows):
  set TICKER=SBRF && python dom_pipe.py     # single ticker
  python dom_pipe.py                         # all 22 tickers

Idempotent: skips tickers already in PG.
"""

import os, sqlite3, sys, time
from datetime import datetime, timezone
import psycopg2
import psycopg2.extras

SQLITE_PATH = os.environ.get("SQLITE_PATH", r"D:\Excavator\Files\excavator_MOEX_DOM.db")
TICKER_FILTER = os.environ.get("TICKER", "").strip().upper()
PG_HOST = os.environ.get("PG_HOST", "10.0.0.60")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASS = os.environ.get("PG_PASS", "postgres")
PG_DB = os.environ.get("PG_DB", "moex")
PG_DSN = os.environ.get("PG_DSN", f"host={PG_HOST} dbname={PG_DB} user={PG_USER} password={PG_PASS}")
BATCH = 20_000  # stable over network, unlike copy_expert

TABLES = [
    ("FINAM-AO.ALLFUTAFLT", "AFLT"), ("FINAM-AO.ALLFUTED",   "ED"),
    ("FINAM-AO.ALLFUTEu",   "EU"),   ("FINAM-AO.ALLFUTFEES", "FEES"),
    ("FINAM-AO.ALLFUTGAZR", "GAZR"), ("FINAM-AO.ALLFUTHYDR", "HYDR"),
    ("FINAM-AO.ALLFUTLKOH", "LKOH"), ("FINAM-AO.ALLFUTMGNT", "MGNT"),
    ("FINAM-AO.ALLFUTMIX",  "MIX"),  ("FINAM-AO.ALLFUTMTSI", "MTSI"),
    ("FINAM-AO.ALLFUTNOTK", "NOTK"), ("FINAM-AO.ALLFUTROSN", "ROSN"),
    ("FINAM-AO.ALLFUTRTKM", "RTKM"), ("FINAM-AO.ALLFUTRTSI", "RTSI"),
    ("FINAM-AO.ALLFUTSBPR", "SBPR"), ("FINAM-AO.ALLFUTSBRF", "SBRF"),
    ("FINAM-AO.ALLFUTSi",   "Si"),   ("FINAM-AO.ALLFUTSNGR", "SNGR"),
    ("FINAM-AO.ALLFUTSNGP", "SNGP"), ("FINAM-AO.ALLFUTTATN", "TATN"),
    ("FINAM-AO.ALLFUTTRNF", "TRNF"), ("FINAM-AO.ALLFUTVTBR", "VTBR"),
]


def log(msg):
    sys.stderr.write(msg)
    sys.stderr.flush()


def main():
    log(f"DSN=postgresql://{PG_USER}@{PG_HOST}/{PG_DB}\n")
    log(f"path={SQLITE_PATH}\n")
    log(f"batch={BATCH}\n\n")

    if not os.path.exists(SQLITE_PATH):
        log(f"NOT FOUND: {SQLITE_PATH}\n")
        sys.exit(1)

    # SQLite connection (local Windows — NORMAL locking)
    sq = sqlite3.connect(SQLITE_PATH, timeout=120)
    sq.execute("PRAGMA busy_timeout = 30000;")
    sq.execute("PRAGMA locking_mode = NORMAL;")
    sq_cur = sq.cursor()

    # PG connection
    pg = psycopg2.connect(PG_DSN)
    pg_cur = pg.cursor()
    log("Connections OK\n\n")

    tables = TABLES
    if TICKER_FILTER:
        tables = [t for t in tables if t[1] == TICKER_FILTER]
        if not tables:
            log(f"TICKER={TICKER_FILTER} not found\n")
            return

    grand_total = 0
    for i, (table_name, ticker) in enumerate(tables, 1):
        log(f"[{i}/{len(tables)}] {table_name} -> {ticker}\n")

        # Idempotency: if data exists, delete and re-import clean
        pg_cur.execute("SELECT count(*) FROM finam_dom_snapshots WHERE ticker=%s", (ticker,))
        existing = pg_cur.fetchone()[0]
        if existing > 0:
            log(f"  found {existing:,} rows in PG, deleting and re-importing...\n")
            pg_cur.execute("DELETE FROM finam_dom_snapshots WHERE ticker=%s", (ticker,))
            pg.commit()

        total = 0
        t0 = time.time()
        last_rowid = 0
        errors = 0

        try:
            while True:
                sq_cur.execute(
                    f'SELECT rowid, time, price, type, volume FROM "{table_name}" '
                    "WHERE rowid > ? ORDER BY rowid LIMIT ?",
                    (last_rowid, BATCH),
                )
                rows = sq_cur.fetchall()
                if not rows:
                    break

                batch = []
                for r in rows:
                    rowid, ts, price, typ, vol = r
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    batch.append((dt, ticker, price, int(typ), vol))
                    last_rowid = rowid

                psycopg2.extras.execute_values(
                    pg_cur,
                    "INSERT INTO finam_dom_snapshots(time, ticker, price, type, volume) VALUES %s",
                    batch,
                    template="(%s::timestamptz, %s, %s, %s, %s)",
                    page_size=len(batch),
                )
                pg.commit()  # commit every batch — don't lose progress on disconnect

                total += len(batch)
                elapsed = time.time() - t0
                log(f"\r  {ticker}: {total:,} rows | {int(total/elapsed):,} r/s")
            elapsed = time.time() - t0
            rate = int(total / elapsed) if elapsed else 0
            log(f"\n  ✓ {ticker}: {total:,} rows in {elapsed:.0f}s ({rate:,} r/s)\n")
            grand_total += total

        except Exception as e:
            pg.rollback()
            log(f"\n  ✗ {ticker} FAILED at row {total:,}: {e}\n")
            errors += 1

    log(f"\n{'='*50}\n")
    log(f"DONE. Total: {grand_total:,} rows, errors={errors}\n")
    sq.close()
    pg.close()


if __name__ == "__main__":
    main()
