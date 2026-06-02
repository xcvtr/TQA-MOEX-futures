#!/usr/bin/env python3
"""Final verification for BR with OI-based sorting."""
import sys, os
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
import requests
from datetime import datetime, timezone, date
from collections import defaultdict

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

print("=== BR contracts (current 2026) ===")
cur.execute("""
    SELECT contract, COUNT(*) as cnt,
           MIN(time)::date as first, MAX(time)::date as last,
           AVG(volume)::int as avg_vol
    FROM moex_prices_5m WHERE symbol = 'BR'
    AND contract NOT LIKE 'GEN_%'
    GROUP BY contract ORDER BY first
""")
for r in cur.fetchall():
    print(f"  {r[0]:15s} cnt={r[1]:>6d}  {r[2]}..{r[3]}  avg_vol={r[4]:>8,}")

print("\n=== BR May 29, 2026 — contract mix ===")
cur.execute("""
    SELECT contract, COUNT(*) as cnt, SUM(volume) as tot_vol
    FROM moex_prices_5m WHERE symbol = 'BR' AND time::date = '2026-05-29'
    GROUP BY contract ORDER BY cnt DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:15s} {r[1]:>4d} bars  vol={r[2]:>8,}")

# Verify vs Alor BR-7.26 (July, highest OI)
JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}

ref_date = date(2026, 5, 29)
ref_ts = int(datetime.combine(ref_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
ref_end = ref_ts + 86400

print(f"\n=== Verification vs Alor BR-7.26 ({ref_date}) ===")
resp = requests.get(
    "https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "BR-7.26", "tf": 300,
             "from": ref_ts, "to": ref_end},
    timeout=15
)
alor = {datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None): c
        for c in resp.json().get("history", [])}

cur.execute("""
    SELECT time, close, volume, contract
    FROM moex_prices_5m WHERE symbol = 'BR' AND time::date = %s AND volume > 0
    ORDER BY time
""", (ref_date,))
db_rows = cur.fetchall()

mismatches = 0
for r in db_rows:
    ts = r[0]
    if ts in alor:
        a = alor[ts]
        if abs(r[1] - a["close"]) > 1:
            mismatches += 1

print(f"  DB: {len(db_rows)} bars, Alor: {len(alor)} bars")
print(f"  Mismatches (>1pt): {mismatches}")
if mismatches == 0:
    print("  ✅ ALL MATCH!")

# Also check: did BR-6.26 data overwrite BR-7.26?
print(f"\n=== Also check vs Alor BR-6.26 ===")
resp = requests.get(
    "https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "BR-6.26", "tf": 300,
             "from": ref_ts, "to": ref_end},
    timeout=15
)
alor_jun = {datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None): c
            for c in resp.json().get("history", [])}

jun_match = 0
jul_match = 0
for r in db_rows:
    ts = r[0]
    if ts in alor:
        a_jul = alor[ts]["close"]
        a_jun = alor_jun.get(ts, {}).get("close")
        if abs(r[1] - a_jul) <= 1:
            jul_match += 1
        elif a_jun and abs(r[1] - a_jun) <= 1:
            jun_match += 1

print(f"  Match BR-7.26 (Jul): {jul_match}/{len(db_rows)} bars")
print(f"  Match BR-6.26 (Jun): {jun_match}/{len(db_rows)} bars")
print(f"  Neither:             {len(db_rows) - jul_match - jun_match}/{len(db_rows)} bars")

# Check count of contracts overall
cur.execute("""
    SELECT contract, open_interest
    FROM moex_prices_5m WHERE symbol = 'BR' AND contract NOT LIKE 'GEN_%'
    GROUP BY contract ORDER BY contract
""")
print(f"\n=== Current BR contracts ===")
for r in cur.fetchall():
    print(f"  {r[0]:15s}")

cur.close()
conn.close()
