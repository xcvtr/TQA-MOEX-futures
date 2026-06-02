#!/usr/bin/env python3
"""Compare Alor 5m data vs MOEX ISS 1m data for Si over the same period."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from datetime import datetime, timezone

JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}

# Pick a specific day: 2025-02-10 (Monday, typical trading day)
# Alor: Si-3.25
# MOEX ISS: can get 1min candles for Si

# 1. ALOR 5m data for Feb 10, 2025
alor_from = int(datetime(2025, 2, 10, tzinfo=timezone.utc).timestamp())
alor_to = int(datetime(2025, 2, 11, tzinfo=timezone.utc).timestamp())

print(f"=== Alor Si-3.25 5m: Feb 10, 2025 ===")
resp = requests.get(
    f"https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "Si-3.25", "tf": 300, "from": alor_from, "to": alor_to},
    timeout=30
)
data = resp.json()
candles = data.get("history", [])
print(f"  {len(candles)} candles")
if candles:
    print(f"  First: ts={candles[0]['time']} O={candles[0]['open']} H={candles[0]['high']} L={candles[0]['low']} C={candles[0]['close']} V={candles[0].get('volume',0)}")
    print(f"  Last:  ts={candles[-1]['time']} O={candles[-1]['open']} H={candles[-1]['high']} L={candles[-1]['low']} C={candles[-1]['close']} V={candles[-1].get('volume',0)}")
    # Check if all are 5-min spaced
    for i in range(1, len(candles)):
        gap = candles[i]['time'] - candles[i-1]['time']
        if gap != 300:
            print(f"  ⚠️  Gap at index {i}: {candles[i-1]['time']} -> {candles[i]['time']} ({gap}s)")
            break
    else:
        print(f"  ✅ All gaps = 300s")

# 2. Also check what Si 1m data looks like from Alor
print(f"\n=== Alor Si-3.25 1m: Feb 10, 2025 ===")
resp = requests.get(
    f"https://api.alor.ru/md/v2/history",
    headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "Si-3.25", "tf": 60, "from": alor_from, "to": alor_to},
    timeout=30
)
data = resp.json()
candles1m = data.get("history", [])
print(f"  {len(candles1m)} candles")
if candles1m:
    print(f"  First: ts={candles1m[0]['time']} O={candles1m[0]['open']} H={candles1m[0]['high']} L={candles1m[0]['low']} C={candles1m[0]['close']} V={candles1m[0].get('volume',0)}")

# 3. Try MOEX ISS API for comparison — Get 1m trade histories
print(f"\n=== MOEX ISS trades for Si-3.25: Feb 10, 2025 ===")
resp = requests.get(
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities/Si-3.25/trades.csv",
    params={
        "iss.meta": "off",
        "iss.only": "trades",
        "from": "2025-02-10",
        "till": "2025-02-11",
    },
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=30
)
if resp.status_code == 200:
    lines = resp.text.strip().split("\n")
    print(f"  {len(lines)} lines (incl. header)")
    if len(lines) > 3:
        print(f"  First: {lines[2]}")
        print(f"  Last:  {lines[-1]}")
    else:
        print(f"  Content: {resp.text[:200]}")
else:
    print(f"  HTTP {resp.status_code}: {resp.text[:200]}")

# 4. Try MOEX ISS orderbook / candles endpoint
print(f"\n=== MOEX ISS candles for Si-3.25 (1min) ===")
resp = requests.get(
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities/Si-3.25/candleborders.json",
    params={"iss.meta": "off"},
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=30
)
if resp.status_code == 200:
    data = resp.json()
    print(json.dumps(data, indent=2)[:500])
else:
    print(f"  HTTP {resp.status_code}")

# 5. Try MOEX ISS dataversion for candles  
print(f"\n=== MOEX ISS dataversion ===")
for tf in [1, 5, 10, 15, 30, 60]:
    resp = requests.get(
        f"https://iss.moex.com/iss/engines/futures/markets/forts/securities/Si-3.25/candleborders.json?interval={tf}",
        params={"iss.meta": "off"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30
    )
    if resp.status_code == 200:
        data = resp.json()
        print(f"  tf={tf}min: {json.dumps(data)[:200]}")
