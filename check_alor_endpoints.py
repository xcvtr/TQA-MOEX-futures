#!/usr/bin/env python3
"""Check ALL possible Alor data sources and alternative candle endpoints."""
import os, json, requests
from datetime import datetime, timezone

JWT = os.getenv("ALOR_JWT", "255375ae-88fa-4f33-bedd-6d9f6a432370")
HEADERS = {"Authorization": f"Bearer {JWT}"}
BASE = "https://api.alor.ru"
NOW = int(datetime.now(tz=timezone.utc).timestamp())

results = {}

# 1. Try GraphQL API
print("=== GraphQL API ===")
QL = """
{
  getHistory(exchange: MOEX, symbol: "Si-6.26", tf: 300, from: %d, to: %d, limits: {limit: 5}) {
    time open high low close volume
  }
}
""" % (NOW - 86400*3, NOW)
r = requests.post(f"{BASE}/md/graphql", headers=HEADERS, json={"query": QL}, timeout=15)
print(f"  status={r.status_code}")
if r.status_code == 200:
    d = r.json()
    print(f"  response: {json.dumps(d)[:300]}")
else:
    print(f"  {r.text[:200]}")

# 2. Try Alor md/v2/history with different TF values (wider range)
print("\n=== Alternative TF formats ===")
for tf in [1, 5, 15, 30, 60, 300, 900, 3600, 86400, 604800]:
    r = requests.get(f"{BASE}/md/v2/history", headers=HEADERS,
        params={"exchange": "MOEX", "symbol": "Si-6.26", "tf": tf,
                 "from": NOW - 86400*2, "to": NOW}, timeout=10)
    if r.status_code == 200:
        d = r.json()
        cnt = len(d.get("history", []))
        print(f"  TF={tf:>6d}s ({tf//60:>3d}min): {cnt:>6d} candles")

# 3. Try different date formats
print("\n=== Different date formats ===")
for fmt, from_val, to_val in [
    ("unix", NOW - 86400, NOW),
    ("ISO", "2025-06-01T00:00:00Z", "2025-06-02T00:00:00Z"),
]:
    r = requests.get(f"{BASE}/md/v2/history", headers=HEADERS,
        params={"exchange": "MOEX", "symbol": "Si-6.26", "tf": 300,
                 "from": from_val, "to": to_val}, timeout=10)
    if r.status_code == 200:
        d = r.json()
        cnt = len(d.get("history", []))
        print(f"  {fmt}: {cnt} candles")

# 4. Check what's in the history response (full sample)
print("\n=== Full history response structure ===")
r = requests.get(f"{BASE}/md/v2/history", headers=HEADERS,
    params={"exchange": "MOEX", "symbol": "Si-6.26", "tf": 300,
             "from": NOW - 3600*6, "to": NOW}, timeout=10)
if r.status_code == 200:
    d = r.json()
    print(f"  Keys: {list(d.keys())}")
    candles = d.get("history", [])
    if candles:
        print(f"  Candle keys: {list(candles[0].keys())}")
        print(f"  First: {candles[0]}")
        print(f"  Last:  {candles[-1]}")

# 5. Try if there's an alternative Alor API host/endpoint for futures
print("\n=== Alternative hosts ===")
for host in ["https://api.alor.ru", "https://apidev.alor.ru", "https://tradeapi.alor.ru"]:
    r = requests.get(f"{host}/md/v2/history", headers=HEADERS,
        params={"exchange": "MOEX", "symbol": "Si-6.26", "tf": 300,
                 "from": NOW - 3600, "to": NOW}, timeout=5)
    print(f"  {host}: status={r.status_code}")

# 6. Check Alor security list for futures
print("\n=== Alor securities by market ===")
for market in ["FUT", "FUTURES", "FORTS", "FOND", "INDEX"]:
    r = requests.get(f"{BASE}/md/v2/securities", headers=HEADERS,
        params={"exchange": "MOEX", "market": market, "limit": 5}, timeout=10)
    if r.status_code == 200:
        d = r.json()
        names = [s.get("symbol","?") for s in (d if isinstance(d, list) else [])]
        print(f"  market={market}: {len(d)} securities, samples={names[:5]}")
    else:
        print(f"  market={market}: {r.status_code}")

# 7. Try getting trades for past sessions (the API doc mentioned this)
print("\n=== Past trades endpoint ===")
r = requests.get(f"{BASE}/md/v2/trades?exchange=MOEX&symbol=Si-6.26&from={NOW-86400}&to={NOW}",
    headers=HEADERS, timeout=10)
print(f"  status={r.status_code}: {r.text[:200]}")
