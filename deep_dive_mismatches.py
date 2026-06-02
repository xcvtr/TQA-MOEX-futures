#!/usr/bin/env python3
"""Deep-dive into BR and Eu mismatches."""
import sys, os
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2
import requests
from datetime import datetime, timezone, date

JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}

ref_date = date(2026, 5, 29)
ref_ts = int(datetime.combine(ref_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
ref_end = ref_ts + 86400

# Check BR with different contract symbols
for sym in ["BR-6.26", "BRM6", "BR-7.26", "BRN6", "BR"]:
    try:
        resp = requests.get(
            "https://api.alor.ru/md/v2/history",
            headers=HEADERS,
            params={"exchange": "MOEX", "symbol": sym, "tf": 300,
                     "from": ref_ts, "to": ref_end},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            cnt = len(data.get("history", []))
            print(f"  Alor '{sym}': {cnt} candles")
            if cnt > 0 and cnt < 10:
                print(f"    Sample: {data['history']}")
        else:
            print(f"  Alor '{sym}': HTTP {resp.status_code}")
    except Exception as e:
        print(f"  Alor '{sym}': error {e}")

# Check BR-7.26 vs DB
print(f"\n=== BR-7.26 vs DB (May 29) ===")
resp = requests.get(
    "https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "BR-7.26", "tf": 300,
             "from": ref_ts, "to": ref_end},
    timeout=10
)
alor_jul = {datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None): c
            for c in resp.json().get("history", [])}

resp = requests.get(
    "https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "BR-6.26", "tf": 300,
             "from": ref_ts, "to": ref_end},
    timeout=10
)
alor_jun = {datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None): c
            for c in resp.json().get("history", [])}

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()
cur.execute("""
    SELECT time, open, high, low, close, volume, contract
    FROM moex_prices_5m WHERE symbol = 'BR' AND time::date = %s
    ORDER BY time
""", (ref_date,))
db_rows = cur.fetchall()
cur.close()
conn.close()

# Show mismatches
print(f"DB vs Alor BR-7.26 (jul) vs Alor BR-6.26 (jun):")
print(f"{'Time':22s} {'DB_close':>10s} {'Alor_jul':>10s} {'Alor_jun':>10s} {'DB_contract':>15s}")
for r in db_rows:
    ts = r[0]
    db_close = r[4]
    db_contract = r[6] or ""
    aj = alor_jul.get(ts, {}).get("close", "—")
    ajn = alor_jun.get(ts, {}).get("close", "—")
    db_match_jul = abs(db_close - aj) <= 1 if isinstance(aj, (int, float)) else False
    db_match_jun = abs(db_close - ajn) <= 1 if isinstance(ajn, (int, float)) else False
    if not db_match_jul and not db_match_jun:
        marker = "⚠️ BOTH MISS"
    elif not db_match_jul:
        marker = "⚠️ JUL MISS"
    elif not db_match_jun:
        marker = "⚠️ JUN MISS"
    else:
        continue  # skip matched
    print(f"  {ts:%H:%M}  {db_close:>10.1f} {aj if isinstance(aj, str) else f'{aj:>10.1f}'} {ajn if isinstance(ajn, str) else f'{ajn:>10.1f}'} {db_contract:>15s}  {marker}")

# Same for Eu
print(f"\n=== Eu-6.26 vs DB (May 29) ===")
resp = requests.get(
    "https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "Eu-6.26", "tf": 300,
             "from": ref_ts, "to": ref_end},
    timeout=10
)
alor_eu = {datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None): c
            for c in resp.json().get("history", [])}

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()
cur.execute("""
    SELECT time, open, high, low, close, volume, contract
    FROM moex_prices_5m WHERE symbol = 'Eu' AND time::date = %s
    ORDER BY time
""", (ref_date,))
db_rows = cur.fetchall()
cur.close()
conn.close()

print(f"{'Time':22s} {'DB_close':>10s} {'Alor_close':>10s} {'DB_contract':>15s}")
mismatches = 0
for r in db_rows:
    ts = r[0]
    db_close = r[4]
    db_contract = r[6] or ""
    ac = alor_eu.get(ts, {}).get("close")
    if ac is None:
        continue
    if abs(db_close - ac) > 1:
        mismatches += 1
        if mismatches <= 10:
            print(f"  {ts:%H:%M}  {db_close:>10.1f} {ac:>10.1f} {db_contract:>15s}  ⚔️ diff={abs(db_close-ac):.1f}")

print(f"Total Eu mismatches: {mismatches}/{len(db_rows)}")
