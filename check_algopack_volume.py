#!/usr/bin/env python3
import os, json, requests, re, sys

with open('/home/user/projects/TQA-MOEX/.env') as f:
    cnt = f.read()
m = re.search(r'ALGOPACK_APIKEY=(.+)', cnt)
TOKEN = m.group(1).strip('"\' \n')
headers = {'Authorization': f'Bearer {TOKEN}'}

print("=== FUTOI ===")
r = requests.get('https://apim.moex.com/iss/analyticalproducts/futoi/securities.json', headers=headers)
data = r.json()
futoi = data.get('futoi', {})
futoi_data = futoi.get('data', [])
tickers_f = set(r[4] for r in futoi_data if len(r) > 4)
print(f'Тикеров: {len(tickers_f)}')
print(f'Строк: {len(futoi_data)}')
print(f'Размер: {sys.getsizeof(r.text)/1024:.1f} KB')

for name, url, label in [
    ('TradeStats', 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2025-06-17', 'Строк'),
    ('OBStats', 'https://apim.moex.com/iss/datashop/algopack/fo/obstats.json?date=2025-06-17', 'Строк'),
    ('OrderStats', 'https://apim.moex.com/iss/datashop/algopack/fo/orderstats.json?date=2025-06-17', 'Строк'),
]:
    print(f'\n=== {name} ===')
    r = requests.get(url, headers=headers)
    j = r.json()
    rows = j.get('data', {}).get('data', [])
    tickers = set(rr[2] for rr in rows if len(rr) > 3)
    print(f'Тикеров: {len(tickers)}')
    print(f'Строк: {len(rows)}')
    print(f'Размер: {sys.getsizeof(r.text)/1024:.1f} KB')
    
    # Сколько дней истории доступно
    dates_key = name.lower() + '.dates'
    dates_data = j.get('data.dates', {})
    if dates_data and isinstance(dates_data, dict):
        avail = dates_data.get('data', [])
        print(f'Доступно дней: {len(avail)}' if avail else '')
