#!/usr/bin/env python3
"""Test: fetch all data for one day without secid filter (all tickers at once)"""
import requests, sys, os

with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    token = None
    for line in f:
        if line.startswith('ALGOPACK_APIKEY='):
            token = line.strip().split('=', 1)[1].strip()
            break

headers = {"Authorization": f"Bearer {token}"}
url = "https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json"

# get all pages for one day
start = 0
total = 0
page = 0
while True:
    params = {"date": "2026-06-17", "limit": 1000, "start": start}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    if r.status_code != 200:
        print(f"HTTP {r.status_code} at start={start}")
        break
    rows = r.json().get("data", {}).get("data", [])
    if not rows:
        break
    total += len(rows)
    page += 1
    start += 1000
    print(f"  Page {page}: {len(rows)} rows (total: {total})")
    if len(rows) < 1000:
        break

print(f"\nDone: {total} rows in {page} pages")
