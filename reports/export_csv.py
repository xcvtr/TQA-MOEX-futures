#!/usr/bin/env python3
# FINAM MOEX DOM: read SQLite on Windows, write CSV to export folder
# Run locally on Windows: python export_csv.py
# pip install psycopg2-binary (не нужно - только stdlib)

import sqlite3
import csv
import os
import time
import sys

SQLITE_PATH = r"D:\Excavator\Files\excavator_MOEX_DOM.db"
EXPORT_DIR = r"D:\Excavator\Files\export"

TICKER_MAP = {
    "FINAM-AO.ALLFUTAFLT": "AFLT", "FINAM-AO.ALLFUTED": "ED",
    "FINAM-AO.ALLFUTEu": "EU", "FINAM-AO.ALLFUTFEES": "FEES",
    "FINAM-AO.ALLFUTGAZR": "GAZR", "FINAM-AO.ALLFUTHYDR": "HYDR",
    "FINAM-AO.ALLFUTLKOH": "LKOH", "FINAM-AO.ALLFUTMGNT": "MGNT",
    "FINAM-AO.ALLFUTMIX": "MIX", "FINAM-AO.ALLFUTMTSI": "MTSI",
    "FINAM-AO.ALLFUTNOTK": "NOTK", "FINAM-AO.ALLFUTROSN": "ROSN",
    "FINAM-AO.ALLFUTRTKM": "RTKM", "FINAM-AO.ALLFUTRTSI": "RTSI",
    "FINAM-AO.ALLFUTSBPR": "SBPR", "FINAM-AO.ALLFUTSBRF": "SBRF",
    "FINAM-AO.ALLFUTSi": "Si", "FINAM-AO.ALLFUTSNGR": "SNGR",
    "FINAM-AO.ALLFUTSNGP": "SNGP", "FINAM-AO.ALLFUTTATN": "TATN",
    "FINAM-AO.ALLFUTTRNF": "TRNF", "FINAM-AO.ALLFUTVTBR": "VTBR",
}

os.makedirs(EXPORT_DIR, exist_ok=True)

con = sqlite3.connect(SQLITE_PATH, timeout=60)
cur = con.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
all_tables = [r[0] for r in cur.fetchall()]

for table in all_tables:
    ticker = TICKER_MAP.get(table)
    if not ticker:
        print(f"SKIP {table} - no mapping")
        continue

    csv_path = os.path.join(EXPORT_DIR, f"{ticker}.csv")
    if os.path.exists(csv_path):
        print(f"SKIP {ticker} - already exported ({os.path.getsize(csv_path):,} bytes)")
        continue

    print(f"EXPORT {ticker}...", end=" ", flush=True)
    t0 = time.time()
    
    with open(csv_path, "w", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["time", "ticker", "price", "type", "volume"])
        
        offset = 0
        batch_size = 50000
        total_rows = 0
        while True:
            cur.execute(
                f'SELECT time, price, type, volume FROM "{table}" ORDER BY time LIMIT ? OFFSET ?',
                (batch_size, offset)
            )
            rows = cur.fetchall()
            if not rows:
                break
            for r in rows:
                writer.writerow([int(r[0]), ticker, r[1], int(r[2]), r[3]])
            total_rows += len(rows)
            offset += batch_size
    
    elapsed = time.time() - t0
    size_mb = os.path.getsize(csv_path) / 1_000_000
    print(f"{total_rows:,} rows, {size_mb:.0f}MB, {elapsed:.0f}s ({total_rows/elapsed:.0f} r/s)")

con.close()
print("\nDONE. All CSVs exported.")
