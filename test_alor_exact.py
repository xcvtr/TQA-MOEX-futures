#!/usr/bin/env python3
"""Test Alor API exactly like the project does."""
import requests, json, os
from datetime import datetime, timezone

JWT = "255375ae-88fa-4f33-bedd-6d9f6a432370"
HEADERS = {"Authorization": f"Bearer {JWT}"}

# Test 1: History - exact params as project
now = int(datetime.now(timezone.utc).timestamp())
params = {"exchange": "MOEX", "symbol": "SS", "tf": 300, "from": now - 86400, "to": now}
resp = requests.get("https://api.alor.ru/md/v2/history", headers=HEADERS, params=params, timeout=15)
print(f"1. History SS: HTTP {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    history = data.get("history", [])
    print(f"   Got {len(history)} candles")
    if history:
        print(f"   First: {json.dumps(history[0], default=str)[:200]}")
else:
    print(f"   {resp.text[:200]}")

# Test 2: Try different symbol format (maybe needs ASSETCODE)
params2 = {"exchange": "MOEX", "symbol": "SMLT", "tf": 300, "from": now - 86400, "to": now}
resp2 = requests.get("https://api.alor.ru/md/v2/history", headers=HEADERS, params=params2, timeout=15)
print(f"\n2. History SMLT (assetcode): HTTP {resp2.status_code}")
if resp2.status_code == 200:
    data2 = resp2.json()
    history2 = data2.get("history", [])
    print(f"   Got {len(history2)} candles")

# Test 3: Try getting current quote
resp3 = requests.get("https://api.alor.ru/md/v2/Securities/SS", headers=HEADERS, timeout=15)
print(f"\n3. Securities: HTTP {resp3.status_code}, {resp3.text[:200]}")

# Test 4: Portfolio 
resp4 = requests.post("https://api.alor.ru/md/v2/client/portfolio", headers=HEADERS, json={}, timeout=15)
print(f"\n4. Portfolio: HTTP {resp4.status_code}, {resp4.text[:200]}")
