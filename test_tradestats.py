#!/usr/bin/env python3
import os, json, requests

# Читаем токен из .env
TOKEN = ''
with open('/home/user/projects/TQA-MOEX/.env') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ALGOPACK_APIKEY='):
            TOKEN = line.split('=', 1)[1]
            break

if not TOKEN:
    print("NO TOKEN")
    exit(1)

headers = {'Authorization': 'Bearer ' + TOKEN}

# TradeStats по Si
print("=== TradeStats SiU5 ===")
r = requests.get('https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2025-06-17&secid=SiU5', headers=headers)
data = r.json()
print('Keys:', list(data.keys()))
rows = data.get('data', [])
print(f'Keys in data: {list(rows.keys())}')
print(f'Всего: {rows}')
    print()

# OBStats по Si
print("=== OBStats SiU5 ===")
r2 = requests.get('https://apim.moex.com/iss/datashop/algopack/fo/obstats.json?date=2025-06-17&secid=SiU5', headers=headers)
data2 = r2.json()
rows2 = data2.get('data', [])
print(f'Rows: {len(rows2)}')
if rows2:
    print('Columns:', data2.get('columns'))
    print('Row 0:', rows2[0])
    print('Row -1:', rows2[-1])
    print()

# OrderStats по Si
print("=== OrderStats SiU5 ===")
r3 = requests.get('https://apim.moex.com/iss/datashop/algopack/fo/orderstats.json?date=2025-06-17&secid=SiU5', headers=headers)
data3 = r3.json()
rows3 = data3.get('data', [])
print(f'Rows: {len(rows3)}')
if rows3:
    print('Columns:', data3.get('columns'))
    print('Row 0:', rows3[0])
    print('Row -1:', rows3[-1])
    print()
