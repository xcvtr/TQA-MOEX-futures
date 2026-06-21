#!/usr/bin/env python3
"""Test: fetch one ticker one day from fo/tradestats"""
import requests, json, sys, os
from datetime import date

# read token
with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    token = None
    for line in f:
        if line.startswith('ALGOPACK_APIKEY='):
            token = line.strip().split('=', 1)[1].strip()
            break

if not token:
    print("NO TOKEN")
    sys.exit(1)

headers = {"Authorization": f"Bearer {token}"}

url = "https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json"
params = {"date": "2026-06-17", "secid": "SiU6", "limit": 5}
r = requests.get(url, params=params, headers=headers, timeout=30)
print(f"Status: {r.status_code}")
if r.status_code == 200:
    j = r.json()
    cols = j.get("data", {}).get("columns", [])
    data = j.get("data", {}).get("data", [])
    print(f"Cols: {len(cols)}, Rows: {len(data)}")
    for row in data:
        d = dict(zip(cols, row))
        print(f"  {d.get('secid')} {d.get('tradedate')} {d.get('tradetime')} "
              f"close={d.get('pr_close')} disb={d.get('disb')} "
              f"vol_b={d.get('vol_b')} vol_s={d.get('vol_s')}")
