#!/usr/bin/env python3
"""Debug: check API data types and CH table schema"""
import clickhouse_connect, requests, os

# read token
with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    token = None
    for line in f:
        if 'ALGOPACK_APIKEY' in line:
            token = line.strip().split('=', 1)[1].strip()
            break

headers = {"Authorization": "Bearer " + token}

# fetch 3 rows from API
url = "https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json"
r = requests.get(url, params={"date": "2026-06-17", "limit": 3}, headers=headers, timeout=30)
data = r.json()["data"]["data"]
cols = r.json()["data"]["columns"]

print("API columns:", cols)
print()
row = data[0]
for c, v in zip(cols, row):
    print(f"  {c:25s} {repr(v)} ({type(v).__name__})")

# Now try to insert via clickhouse_connect
ch = clickhouse_connect.get_client(host="10.0.0.63", port=8123, database="moex")

# Check table schema
schema = ch.query("DESCRIBE tradestats_fo").result_rows
print("\nCH table schema:")
for r in schema:
    print(f"  {r[0]:25s} {r[1]}")
