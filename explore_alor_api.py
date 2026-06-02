#!/usr/bin/env python3
"""Explore Alor API endpoints systematically."""
import os, json, requests
from datetime import datetime, timezone

JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}
BASE = "https://api.alor.ru"

NOW_TS = int(datetime.now(tz=timezone.utc).timestamp())

endpoints = [
    # 1. secinfo
    ("secinfo", f"{BASE}/md/v2/secinfo/MOEX/Si-6.26", {}),
    # 2. history/daily  
    ("daily", f"{BASE}/md/v2/history/daily",
     {"exchange": "MOEX", "symbol": "Si-6.26", "from": "2025-06-01", "to": "2025-06-10"}),
    # 3. AllTrades (recent)
    ("alltrades", f"{BASE}/md/v2/AllTrades",
     {"exchange": "MOEX", "symbol": "Si-6.26",
      "from": NOW_TS - 600, "to": NOW_TS}),
    # 4. 1h candles
    ("1h candles", f"{BASE}/md/v2/history",
     {"exchange": "MOEX", "symbol": "Si-6.26", "tf": 3600,
      "from": int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp()),
      "to": int(datetime(2025, 6, 2, tzinfo=timezone.utc).timestamp())}),
    # 5. orderbook
    ("orderbook", f"{BASE}/md/v2/orderbook/MOEX/Si-6.26", {"depth": 10}),
    # 6. risk
    ("risk", f"{BASE}/md/v2/risk/MOEX/Si-6.26", {}),
    # 7. Quote
    ("quote", f"{BASE}/md/v2/quote/MOEX/Si-6.26", {}),
    # 8. Try WAP (weighted avg price) 
    ("daily/1", f"{BASE}/md/v2/daily/1/Si-6.26",
     {"exchange": "MOEX", "from": "2025-06-01", "to": "2025-06-10"}),
]

for name, url, params in endpoints:
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = resp.json() if resp.text else {}
        print(f"\n=== {name} ({resp.status_code}) ===")
        if isinstance(data, dict):
            print(json.dumps(data, indent=2)[:300])
        elif isinstance(data, list):
            print(f"  {len(data)} items")
            if data:
                print(json.dumps(data[0], indent=2)[:200])
        else:
            print(f"  {data}")
    except Exception as e:
        print(f"\n=== {name} FAILED: {e} ===")
