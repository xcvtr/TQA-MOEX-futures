#!/usr/bin/env python3
import os, json, requests, re

with open('/home/user/projects/TQA-MOEX/.env') as f:
    cnt = f.read()
m = re.search(r'ALGOPACK_APIKEY=(.+)', cnt)
TOKEN = m.group(1).strip("'\" \n")
headers = {'Authorization': 'Bearer ' + TOKEN}

for name, url in [
    ('TradeStats', 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json'),
    ('OBStats', 'https://apim.moex.com/iss/datashop/algopack/fo/obstats.json'),
    ('OrderStats', 'https://apim.moex.com/iss/datashop/algopack/fo/orderstats.json'),
]:
    r = requests.get(url, params={'date': '2025-06-17', 'secid': 'SiU5'}, headers=headers)
    j = r.json()
    d = j.get('data', {})
    rows = d.get('data', [])
    cursor = j.get('data.cursor', {})
    dates = j.get('data.dates', {})
    
    print(f'=== {name} ===')
    print(f'  SiU5 rows: {len(rows)}')
    if cursor and isinstance(cursor, dict) and 'data' in cursor:
        print(f'  Cursor: {cursor["data"]}')
    if dates and isinstance(dates, dict) and 'data' in dates:
        print(f'  Доступно дней: {len(dates["data"])}')
        if dates['data']:
            print(f'  Первый: {dates["data"][0]}, последний: {dates["data"][-1]}')
    print()
