#!/usr/bin/env python3
import os, json, requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ.get('ALGOPACK_APIKEY')
if not TOKEN:
    print("NO TOKEN in ALGOPACK_APIKEY")
    exit(1)

headers = {'Authorization': f'Bearer {TOKEN}'}

tests = [
    ('FUTOI', 'https://apim.moex.com/iss/datashop/algopack/fut/futoi.json?date=2025-06-17'),
    ('OBStats', 'https://apim.moex.com/iss/datashop/algopack/fut/obstats.json?date=2025-06-17'),
    ('TradeStats', 'https://apim.moex.com/iss/datashop/algopack/fut/tradestats.json?date=2025-06-17'),
    ('OrderStats', 'https://apim.moex.com/iss/datashop/algopack/fut/orderstats.json?date=2025-06-17'),
    ('HI2', 'https://apim.moex.com/iss/datashop/algopack/fut/hi2.json?date=2025-06-16'),
]

for name, url in tests:
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f'{name}: status={r.status_code}')
        if r.status_code == 200:
            if r.text.strip().startswith('<'):
                print(f'  HTML ответ (скорее всего неавторизован)')
            else:
                data = r.json()
                if 'data' in data and data['data']:
                    recs = data['data']
                    print(f'  Записей: {len(recs)}')
                    print(f'  Первая: {json.dumps(recs[0], ensure_ascii=False)[:300]}')
                else:
                    print(f'  Ответ: {r.text[:300]}')
        else:
            print(f'  Ошибка: {r.text[:200]}')
    except Exception as e:
        print(f'{name}: error: {e}')
    print()
