#!/usr/bin/env python3
"""Verify Eu and BR after reload."""
import sys, os
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
from collections import defaultdict

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

for sym in ("Eu", "BR"):
    print(f"\n=== {sym} ===")
    
    # Contract mix
    cur.execute("""
        SELECT contract, COUNT(*) as cnt,
               MIN(time)::date as first, MAX(time)::date as last,
               AVG(volume)::int as avg_vol
        FROM moex_prices_5m WHERE symbol = %s
        GROUP BY contract ORDER BY first
    """, (sym,))
    print(f"{'Contract':25s} {'Rows':>8s} {'First':>12s} {'Last':>12s} {'AvgVol':>8s}")
    for r in cur.fetchall():
        print(f"  {r[0]:25s} {r[1]:>8,d} {str(r[2]):>12s} {str(r[3]):>12s} {r[4]:>8,}")
    
    # Check for empty contracts
    cur.execute("SELECT COUNT(*) FROM moex_prices_5m WHERE symbol = %s AND (contract IS NULL OR contract = '')", (sym,))
    empty = cur.fetchone()[0]
    print(f"  Empty contracts: {empty}")
    
    # Check May 29 contract mix
    cur.execute("""
        SELECT contract, COUNT(*) as cnt, SUM(volume) as tot_vol
        FROM moex_prices_5m WHERE symbol = %s AND time::date = '2026-05-29'
        GROUP BY contract ORDER BY cnt DESC
    """, (sym,))
    print(f"\n  May 29, 2026 — contract mix:")
    for r in cur.fetchall():
        print(f"    {r[0]:25s} {r[1]:>4d} bars  vol={r[2]:>8,}")

# Compare Eu data vs Alor for May 29
import requests
from datetime import datetime, timezone, date

JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}

ref_date = date(2026, 5, 29)
ref_ts = int(datetime.combine(ref_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
ref_end = ref_ts + 86400

print(f"\n=== Verification vs Alor fresh data ({ref_date}) ===")

for sym, alor_sym in [("Eu", "Eu-6.26"), ("BR", "BR-7.26")]:
    resp = requests.get(
        "https://api.alor.ru/md/v2/history",
        headers=HEADERS,
        params={"exchange": "MOEX", "symbol": alor_sym, "tf": 300,
                 "from": ref_ts, "to": ref_end},
        timeout=15
    )
    alor_data = resp.json()
    alor_candles = {datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None): c
                    for c in alor_data.get("history", [])}
    
    cur.execute("""
        SELECT time, open, high, low, close, volume, contract
        FROM moex_prices_5m WHERE symbol = %s AND time::date = %s AND volume > 0
        ORDER BY time
    """, (sym, ref_date))
    db_rows = cur.fetchall()
    
    common = 0
    mismatches = 0
    for r in db_rows:
        ts = r[0]
        if ts in alor_candles:
            common += 1
            a = alor_candles[ts]
            if abs(r[4] - a["close"]) > 1:
                mismatches += 1
                if mismatches <= 5:
                    print(f"  {sym} {ts:%H:%M}: DB close={r[4]} Alor={a['close']} v={r[5]} ctr={r[6]}")
    
    print(f"  {sym}/{alor_sym}: {len(db_rows)} DB bars, {len(alor_candles)} Alor bars, {common} common, {mismatches} mismatches")

cur.close()
conn.close()
