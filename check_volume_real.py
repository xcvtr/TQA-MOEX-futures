#!/usr/bin/env python3
import os, json, requests, re

with open('/home/user/projects/TQA-MOEX/.env', 'rb') as f:
    for line in f:
        line = line.decode('utf-8')
        if line.startswith('ALGOPACK_APIKEY'):
            _, val = line.split('=', 1)
            TOKEN=***(1).strip('"\' \n')
            break

headers = {'Authorization': 'Bearer ' + TOKEN}
base = 'https://apim.moex.com/iss/datashop/algopack/fo'

for name, url in [
    ('TradeStats', base + '/tradestats.json'),
    ('OBStats',    base + '/obstats.json'),
]:
    r = requests.get(url, params={'date': '2025-06-17'}, headers=headers, timeout=15)
    j = r.json()
    cursor = j.get('data.cursor', {}).get('data', [])
    total = cursor[0][1] if cursor else 0
    kb = len(r.text)/1024
    d = j.get('data', {})
    rows = d.get('data', [])
    tickers = len(set(rr[2] for rr in rows if len(rr) > 3)) if rows else 0
    print(f'{name}: {total} rows ({kb:.0f} KB), tickers={tickers}')

# FUTOI
r2 = requests.get('https://apim.moex.com/iss/analyticalproducts/futoi/securities.json', headers=headers, timeout=15)
j2 = r2.json()
fut = j2.get('futoi', {})
fut_data = fut.get('data', [])
print(f'FUTOI: {len(fut_data)} rows, {len(r2.text)/1024:.0f} KB')
