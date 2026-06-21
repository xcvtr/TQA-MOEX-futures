#!/usr/bin/env python3
import os, json, requests
import re

with open('/home/user/projects/TQA-MOEX/.env') as f:
    content = f.read()

m = re.search(r'ALGOPACK_APIKEY=(.+)', content)
if not m:
    print("NO TOKEN FOUND")
    exit(1)

TOKEN = m.group(1).strip('"\' \\n')
headers = {'Authorization': f'Bearer {TOKEN}'}

for name, url in [
    ('TradeStats SiU5', 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2025-06-17&secid=SiU5'),
    ('OBStats SiU5', 'https://apim.moex.com/iss/datashop/algopack/fo/obstats.json?date=2025-06-17&secid=SiU5'),
    ('OrderStats SiU5', 'https://apim.moex.com/iss/datashop/algopack/fo/orderstats.json?date=2025-06-17&secid=SiU5'),
]:
    print(f'\n=== {name} ===')
    r = requests.get(url, headers=headers)
    data = r.json()
    print('Keys:', list(data.keys()))
    d = data.get('data', {})
    if isinstance(d, dict):
        print('data keys:', list(d.keys()))
        for k in list(d.keys()):
            v = d[k]
            if isinstance(v, list):
                print(f'  {k}: {len(v)} rows')
                if v: print(f'    First: {json.dumps(v[0], ensure_ascii=False)[:500]}')
            else:
                print(f'  {k}: {v}')
    elif isinstance(d, list):
        print(f'data[]: {len(d)} rows')
        if d: print(f'  First: {json.dumps(d[0], ensure_ascii=False)[:500]}')
