#!/usr/bin/env python3
"""Debug insert3: try with column_names"""
import clickhouse_connect, requests, os
from datetime import date, datetime

with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    token = None
    for line in f:
        if 'ALGOPACK_APIKEY' in line:
            token = line.strip().split('=', 1)[1].strip()
            break

headers = {"Authorization": "Bearer " + token}

url = "https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json"
r = requests.get(url, params={"date": "2026-06-17", "limit": 3}, headers=headers, timeout=30)
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

# try insert as list of lists with column_names
ch_data = []
for row in rows:
    row_data = []
    for c, v in zip(cols, row):
        if c in convs:
            row_data.append(convs[c](v))
        else:
            row_data.append(v if v != '' else None)
    ch_data.append(row_data)

print("ch_data[0]:", ch_data[0])

ch = clickhouse_connect.get_client(host="10.0.0.63", port=8123, database="moex")
try:
    ch.insert("tradestats_fo", ch_data, column_names=cols)
    print("INSERT OK!")
except Exception as e:
    print(f"INSERT ERROR: {e}")
