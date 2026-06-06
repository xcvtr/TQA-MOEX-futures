#!/usr/bin/env python3
"""Test Alor API v2 endpoints for trading capability."""
import urllib.request, json, os
from datetime import datetime, timezone

JWT = "255375ae-88fa-4f33-bedd-6d9f6a432370"
HEADERS = {"Authorization": f"Bearer {JWT}"}

def test(url, method="GET", data=None):
    headers = HEADERS.copy()
    if data:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print(f"  [{resp.status}] {body[:400]}")
            return body
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"  [HTTP {e.code}] {body}")
        return None
    except Exception as e:
        print(f"  [ERR] {e}")
        return None

# First verify history still works
now = int(datetime.now(timezone.utc).timestamp())
print("1. History (should work):")
test(f"https://api.alor.ru/md/v2/history?exchange=MOEX&symbol=SS&tf=300&from={now-86400}&to={now}")

# Try quotes via history with 1m
print("\n2. 1m quotes:")
test(f"https://api.alor.ru/md/v2/history?exchange=MOEX&symbol=SS&tf=60&from={now-3600}&to={now}")

# Try portfolio with the correct format
print("\n3. Portfolio (POST):")
test("https://api.alor.ru/md/v2/client/portfolio", method="POST", data={})

# Try orders endpoint
print("\n4. List orders:")
for ep in [
    "https://api.alor.ru/commandapi/v2/client/orders",
    "https://api.alor.ru/commandapi/v2/user/orders",
]:
    print(f"  {ep}")
    test(ep)

# Try new Alor API v2 format
print("\n5. Client/account:")
test("https://api.alor.ru/md/v2/client/accounts")

# Check available trading APIs
print("\n6. Check Alor API docs endpoint:")
test("https://api.alor.ru/docs")
