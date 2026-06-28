#!/usr/bin/env python3
"""FINAM MOEX DOM -> PostgreSQL. Run on Windows via SSH.
Reads SQLite in mode=ro (no lock conflict with collector).
Writes directly to PG via psycopg2 COPY.

Usage: python finam_dom_import.py
pip install psycopg2-binary (already done)
"""

import sqlite3, psycopg2, psycopg2.extras, datetime, os, sys, time

SQLITE_PATH = r"D:\Excavator\Files\excavator_MOEX_DOM.db"
PG_DSN = "host=10.0.0.60 dbname=moex user=postgres password=postgres"
BATCH = 20000

TICKERS = [
    ("FINAM-AO.ALLFUTAFLT","AFLT"),("FINAM-AO.ALLFUTED","ED"),
    ("FINAM-AO.ALLFUTEu","EU"),("FINAM-AO.ALLFUTFEES","FEES"),
    ("FINAM-AO.ALLFUTGAZR","GAZR"),("FINAM-AO.ALLFUTHYDR","HYDR"),
    ("FINAM-AO.ALLFUTLKOH","LKOH"),("FINAM-AO.ALLFUTMGNT","MGNT"),
    ("FINAM-AO.ALLFUTMIX","MIX"),("FINAM-AO.ALLFUTMTSI","MTSI"),
    ("FINAM-AO.ALLFUTNOTK","NOTK"),("FINAM-AO.ALLFUTROSN","ROSN"),
    ("FINAM-AO.ALLFUTRTKM","RTKM"),("FINAM-AO.ALLFUTRTSI","RTSI"),
    ("FINAM-AO.ALLFUTSBPR","SBPR"),("FINAM-AO.ALLFUTSBRF","SBRF"),
    ("FINAM-AO.ALLFUTSi","Si"),("FINAM-AO.ALLFUTSNGR","SNGR"),
    ("FINAM-AO.ALLFUTSNGP","SNGP"),("FINAM-AO.ALLFUTTATN","TATN"),
    ("FINAM-AO.ALLFUTTRNF","TRNF"),("FINAM-AO.ALLFUTVTBR","VTBR"),
]

def main():
    sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1)
    sys.stderr = open(sys.stderr.fileno(), 'w', buffering=1)

    print(f"SQLite: {SQLITE_PATH}")
    print(f"PG:     {PG_DSN}")
    print()

    if not os.path.exists(SQLITE_PATH):
        print(f"NOT FOUND: {SQLITE_PATH}")
        sys.exit(1)

    sq = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True, timeout=30)
    sq_cur = sq.cursor()

    pg = psycopg2.connect(PG_DSN)
    pg_cur = pg.cursor()

    grand_total = 0
    for i, (table_name, ticker) in enumerate(TICKERS, 1):
        print(f"[{i}/{len(TICKERS)}] {table_name} -> {ticker}")
        total = 0
        t0 = time.time()

        try:
            sq_cur.execute(f'SELECT time, price, type, volume FROM "{table_name}"')
            while True:
                rows = sq_cur.fetchmany(BATCH)
                if not rows:
                    break

                batch = []
                for r in rows:
                    ts = datetime.datetime.fromtimestamp(r[0], tz=datetime.timezone.utc)
                    batch.append((ts, ticker, r[1], int(r[2]), r[3]))

                psycopg2.extras.execute_values(
                    pg_cur,
                    "INSERT INTO finam_dom_snapshots(time, ticker, price, type, volume) VALUES %s",
                    batch,
                    template="(%s::timestamptz, %s, %s, %s, %s)"
                )

                total += len(batch)
                elapsed = time.time() - t0
                print(f"\r  {ticker}: {total:,} rows | {int(total/elapsed):,} r/s", end="")

            pg.commit()
            elapsed = time.time() - t0
            print(f"\n  OK {ticker}: {total:,} rows in {elapsed:.0f}s ({int(total/elapsed):,} r/s)")
            grand_total += total

        except Exception as e:
            pg.rollback()
            print(f"\n  ERROR {ticker}: {e}")

    print(f"\n{'='*50}")
    print(f"DONE. Total: {grand_total:,} rows.")
    sq.close()
    pg.close()

if __name__ == "__main__":
    main()
