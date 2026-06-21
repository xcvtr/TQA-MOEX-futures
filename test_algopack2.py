#!/usr/bin/env python3
import os, json, requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ.get('ALGOPACK_APIKEY', '')
if not TOKEN:
    print("NO TOKEN")
    exit(1)

headers = {'Authorization': f'Bearer {TOKEN}'}

tests = [
    ('FUTOI все', 'https://apim.moex.com/iss/analyticalproducts/futoi/securities.json'),
    ('FUTOI Si', 'https://apim.moex.com/iss/analyticalproducts/futoi/securities/SiU5.json'),
    ('OBStats FUT', 'https://apim.moex.com/iss/datashop/algopack/fut/obstats.json?date=2025-06-17&secid=SiU5'),
    ('TradeStats FUT', 'https://apim.moex.com/iss/datashop/algopack/fut/tradestats.json?date=2025-06-17&secid=SiU5'),
    ('Candles FUT', 'https://apim.moex.com/iss/datashop/algopack/fut/candles.json?date=2025-06-17&secid=SiU5'),
    ('OrderBook FUT', 'https://apim.moex.com/iss/datashop/algopack/fut/orderbook.json?date=2025-06-17&secid=SiU5'),
    ('Trades FUT', 'https://apim.moex.com/iss/datashop/algopack/fut/trades.json?date=2025-06-17&secid=SiU5'),
    ('HI2', 'https://apim.moex.com/iss/analyticalproducts/hi2/securities.json?date=2025-06-16'),
]

for name, url in tests:
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f'{name}: status={r.status_code}')
        if r.status_code == 200:
            if r.text.strip().startswith('<'):
                print(f'  HTML, ошибка авторизации')
            else:
                try:
                    data = r.json()
                    if isinstance(data, dict) and 'data' in data and data['data']:
                        recs = data['data']
                        print(f'  Записей: {len(recs)}')
                        print(f'  Первая: {json.dumps(recs[0], ensure_ascii=False)[:300]}')
                    elif isinstance(data, list):
                        print(f'  Массив, {len(data)} элементов')
                        if data:
                            print(f'  Первая: {json.dumps(data[0], ensure_ascii=False)[:300]}')
                    elif isinstance(data, dict):
                        keys = list(data.keys())
                        print(f'  Ключи: {keys[:8]}')
                        if 'candles' in data or 'securities' in data:
                            arr = data.get('candles') or data.get('securities') or []
                            print(f'  Элементов: {len(arr)}')
                        else:
                            print(f'  Текст: {r.text[:300]}')
                except json.JSONDecodeError:
                    print(f'  Не JSON: {r.text[:200]}')
        else:
            print(f'  Ошибка: {r.text[:200]}')
    except Exception as e:
        print(f'{name}: error: {e}')
    print()
