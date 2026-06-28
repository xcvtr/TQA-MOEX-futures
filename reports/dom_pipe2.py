#!/usr/bin/env python3
"""Fast sequential read - tables are time-ordered by append pattern."""
import sqlite3, sys, time

SQLITE_PATH = r"D:\Excavator\Files\excavator_MOEX_DOM.db"

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
    con = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True, timeout=10)
    cur = con.cursor()

    sys.stdout.write("time,ticker,price,type,volume\n")

    for table_name, ticker in TICKERS:
        total = 0
        t0 = time.time()

        # Sequential read - data in insertion (time) order
        cur.execute(f'SELECT time, price, type, volume FROM "{table_name}"')
        # fetchmany with cursor iteration to avoid loading all into memory
        while True:
            rows = cur.fetchmany(10000)
            if not rows:
                break
            for r in rows:
                sys.stdout.write(f"{int(r[0])},{ticker},{r[1]},{int(r[2])},{r[3]}\n")
            total += len(rows)

        elapsed = time.time() - t0
        rate = int(total / elapsed) if elapsed else 0
        sys.stderr.write(f"\r{ticker}: {total:,} rows, {elapsed:.0f}s, {rate:,} r/s\n")
        sys.stderr.flush()

    con.close()

if __name__ == "__main__":
    main()
