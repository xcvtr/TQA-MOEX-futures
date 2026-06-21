#!/usr/bin/env python3
"""Debug insert: show exact error"""
import clickhouse_connect, requests, os
from datetime import date, datetime

with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    token = None
    for line in f:
        if 'ALGOPACK_APIKEY' in line:
            token = line.strip().split('=', 1)[1].strip()
            break

headers = {"Authorization": "Bearer " + token}

# fetch 1 page
url = "https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json"
r = requests.get(url, params={"date": "2026-06-17", "limit": 5}, headers=headers, timeout=30)
rows = r.json()["data"]["data"]
cols = r.json()["data"]["columns"]

def conv_tradedate(v):
    if v is None or v == '': return None
    return date.fromisoformat(v) if isinstance(v, str) else v

def conv_systime(v):
    if v is None or v == '': return None
    if isinstance(v, str):
        v = v.replace('T', ' ')
        if '.' in v:
            return datetime.strptime(v, '%Y-%m-%d %H:%M:%S.%f')
        return datetime.strptime(v, '%Y-%m-%d %H:%M:%S')
    return v

convs = {'tradedate': conv_tradedate, 'SYSTIME': conv_systime}

data = []
for row in rows:
    d = {}
    for c, v in zip(cols, row):
        if c in convs:
            d[c] = convs[c](v)
        else:
            d[c] = v if v != '' else None
    data.append(d)

# print first row
print("Converted row 0:")
for k, v in data[0].items():
    print(f"  {k:25s} {repr(v)} ({type(v).__name__})")

# try insert
ch = clickhouse_connect.get_client(host="10.0.0.63", port=8123, database="moex")
try:
    ch.insert("tradestats_fo", data)
    print("\nINSERT OK!")
except Exception as e:
    print(f"\nINSERT ERROR: {e}")
    print(f"Error type: {type(e).__name__}")
