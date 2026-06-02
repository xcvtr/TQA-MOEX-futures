#!/usr/bin/env python3
"""Compare DB data vs fresh Alor fetch for Si on Feb 10, 2025."""
import sys, os, json
from datetime import datetime, timezone, date
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
import requests
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2

JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}

target_date = date(2025, 2, 10)

# 1. Get data from DB
conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()
cur.execute("""
    SELECT time, open, high, low, close, volume, contract
    FROM moex_prices_5m
    WHERE symbol = 'Si' AND time::date = %s
    ORDER BY time
""", (target_date,))
db_rows = cur.fetchall()
cur.close()
conn.close()

print(f"=== DB Si data: {target_date} ===")
print(f"  {len(db_rows)} bars")
if db_rows:
    print(f"  First: time={db_rows[0][0]} O={db_rows[0][1]} C={db_rows[0][4]} V={db_rows[0][5]}")
    print(f"  Last:  time={db_rows[-1][0]} O={db_rows[-1][1]} C={db_rows[-1][4]} V={db_rows[-1][5]}")
    contracts = set(r[6] for r in db_rows if r[6])
    print(f"  Contracts: {contracts}")
else:
    print("  EMPTY!")

# 2. Get fresh data from Alor for Si-3.25
alor_from = int(datetime(2025, 2, 10, tzinfo=timezone.utc).timestamp())
alor_to = int(datetime(2025, 2, 11, tzinfo=timezone.utc).timestamp())

resp = requests.get(
    "https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "Si-3.25", "tf": 300, "from": alor_from, "to": alor_to},
    timeout=30
)
alor_data = resp.json()
alor_candles = alor_data.get("history", [])

print(f"\n=== Fresh Alor Si-3.25: {target_date} ===")
print(f"  {len(alor_candles)} bars")
if alor_candles:
    first_ts = datetime.fromtimestamp(alor_candles[0]["time"], tz=timezone.utc)
    last_ts = datetime.fromtimestamp(alor_candles[-1]["time"], tz=timezone.utc)
    print(f"  First: {first_ts} O={alor_candles[0]['open']} C={alor_candles[0]['close']} V={alor_candles[0].get('volume',0)}")
    print(f"  Last:  {last_ts} O={alor_candles[-1]['open']} C={alor_candles[-1]['close']} V={alor_candles[-1].get('volume',0)}")

# 3. Compare by timestamp alignment
print(f"\n=== Comparison ===")
if db_rows and alor_candles:
    db_map = {}
    for r in db_rows:
        db_map[r[0]] = {"open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5], "contract": r[6]}

    alor_map = {}
    for c in alor_candles:
        ts = datetime.fromtimestamp(c["time"], tz=timezone.utc).replace(tzinfo=None)
        alor_map[ts] = {"open": c["open"], "high": c["high"], "low": c["low"],
                         "close": c["close"], "volume": c.get("volume", 0)}

    common = sorted(set(db_map.keys()) & set(alor_map.keys()))
    only_db = sorted(set(db_map.keys()) - set(alor_map.keys()))
    only_alor = sorted(set(alor_map.keys()) - set(db_map.keys()))

    print(f"  Common timestamps: {len(common)}")
    print(f"  Only in DB:        {len(only_db)}")
    print(f"  Only in Alor:      {len(only_alor)}")

    if only_db:
        print(f"  DB-only samples:  {only_db[:5]}")
    if only_alor:
        print(f"  Alor-only samples: {only_alor[:5]}")

    # Check price match
    mismatches = 0
    for ts in common:
        d = db_map[ts]
        a = alor_map[ts]
        if abs(d["open"] - a["open"]) > 1 or abs(d["close"] - a["close"]) > 1:
            mismatches += 1
            if mismatches <= 5:
                print(f"  ⚔️ {ts}: DB(O={d['open']} H={d['high']} L={d['low']} C={d['close']} V={d['volume']})")
                print(f"          Alor(O={a['open']} H={a['high']} L={a['low']} C={a['close']} V={a['volume']})")

    print(f"\n  Mismatches (>1pt): {mismatches}/{len(common)}")
    if mismatches == 0:
        print("  ✅ All prices match!")
    elif mismatches < 10:
        print("  ⚠️  Minor mismatches")

    # Compare avg close
    db_avg_close = sum(d["close"] for d in db_map.values()) / len(db_map)
    alor_avg_close = sum(a["close"] for a in alor_map.values()) / len(alor_map)
    print(f"\n  DB avg close:   {db_avg_close:.2f}")
    print(f"  Alor avg close: {alor_avg_close:.2f}")
    print(f"  Diff:           {abs(db_avg_close - alor_avg_close):.2f} pts")
