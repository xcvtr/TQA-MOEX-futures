#!/usr/bin/env python3
"""Test Alor API trading endpoints."""
import urllib.request, json, os

JWT = "255375ae-88fa-4f33-bedd-6d9f6a432370"
HEADERS = {"Authorization": f"Bearer {JWT}"}

def test(url, data=None):
    req = urllib.request.Request(url, headers=HEADERS, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print(f"  HTTP {resp.status}: {body[:300]}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.reason}")
        body = e.read().decode()[:200]
        if body:
            print(f"  Body: {body}")
    except Exception as e:
        print(f"  Error: {e}")

# 1. Market data endpoint (known working)
print("\n1. Quotes (MD):")
test("https://api.alor.ru/md/v2/quotes/SS?exchange=MOEX")

# 2. History (known working)
print("\n2. History:")
test("https://api.alor.ru/md/v2/history?symbol=SS&exchange=MOEX&tf=60&from=20260601000000&to=20260603000000")

# 3. Client info
print("\n3. Client info:")
test("https://api.alor.ru/md/v2/client/info")

# 4. Portfolio
print("\n4. Portfolio:")
test("https://api.alor.ru/md/v2/client/portfolio", data=b'{}')

# 5. Accounts (trading)
print("\n5. Trading accounts:")
for ep in ['https://api.alor.ru/commandapi/v2/client/accounts',
           'https://api.alor.ru/war/v2/client/accounts',
           'https://api.alor.ru/md/v2/client/accounts']:
    print(f"  {ep}")
    test(ep)

# 6. Check if this is a read-only token by trying the refresh endpoint
print("\n6. Token info:")
test("https://api.alor.ru/md/v2/client/risk")
