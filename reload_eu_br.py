#!/usr/bin/env python3
"""Delete and reload Eu/BR data with fixed contract logic."""
import os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
conn.autocommit = False
cur = conn.cursor()

for sym in ("Eu", "BR"):
    # Count before
    cur.execute("SELECT COUNT(*) FROM moex_prices_5m WHERE symbol = %s", (sym,))
    before = cur.fetchone()[0]
    
    # Delete
    cur.execute("DELETE FROM moex_prices_5m WHERE symbol = %s", (sym,))
    conn.commit()
    
    # Count after
    cur.execute("SELECT COUNT(*) FROM moex_prices_5m WHERE symbol = %s", (sym,))
    after = cur.fetchone()[0]
    
    print(f"{sym}: deleted {before - after} rows (before={before}, after={after})")

cur.close()
conn.close()
print("\nNow re-running price_history_5m.py...")
