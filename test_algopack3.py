#!/usr/bin/env python3
import os, json, requests
from dotenv import load_dotenv

load_dotenv()
TOKEN=***KEY', '')
headers = {'Authorization': f'Bearer {TOKEN}'}

tests = [
    ('FUTOI все', 'https://apim.moex.com/iss/analyticalproducts/futoi/securities.json'),
    ('FUTOI Si', 'https://apim.moex.com/iss/analyticalproducts/futoi/securities/SiU5.json'),
    ('TradeStats все', 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2025-06-17'),
    ('TradeStats Si', 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2025-06-17&secid=SiU5'),
    ('OBStats все', 'https://apim.moex.com/iss/datashop/algopack/fo/obstats.json?date=2025-06-17'),
    ('OBStats Si', 'https://apim.moex.com/iss/datashop/algopack/fo/obstats.json?date=2025-06-17&secid=SiU5'),
    ('OrderStats все', 'https://apim.moex.com/iss/datashop/algopack/fo/orderstats.json?date=2025-06-17'),
    ('OrderStats Si', 'https://apim.moex.com/iss/datashop/algopack/fo/orderstats.json?date=2025-06-17&secid=SiU5'),
    ('Candles Si', 'https://apim.moex.com/iss/datashop/algopack/fo/candles.json?date=2025-06-17&secid=SiU5'),
    ('OrderBook Si', 'https://apim.moex.com/iss/datashop/algopack/fo/orderbook.json?date=2025-06-17&secid=SiU5'),
    ('Trades Si', 'https://apim.moex.com/iss/datashop/algopack/fo/trades.json?date=2025-06-17&secid=SiU5'),
    ('HI2', 'https://apim.moex.com/iss/analyticalproducts/hi2/securities.json?date=2025-06-16'),
]

for name, url in tests:
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f'{name}: status={r.status_code}')
        if r.status_code == 200:
            if r.text.strip().startswith('<'):
                print(f'  HTML, ошибка')
            else:
                try:
                    data = r.json()
                    if isinstance(data, list):
                        print(f'  Массив, {len(data)} эл.')
                        if data:
                            k = list(data[0].keys()) if isinstance(data[0], dict) else []
                            print(f'  Ключи: {k}')
                    elif isinstance(data, dict):
                        keys = list(data.keys())
                        if 'data' in data and isinstance(data['data'], list):
                            print(f'  data[]: {len(data["data"])} записей')
                            if data['data']:
                                print(f'  Первая: {json.dumps(data["data"][0], ensure_ascii=False)[:300]}')
                        elif 'futoi' in data:
                            fut = data['futoi']
                            if isinstance(fut, dict) and 'columns' in fut:
                                print(f'  Колонки: {fut["columns"]}')
                                print(f'  Данных: {len(fut.get("data",[]))} строк')
                                if fut.get('data'):
                                    print(f'  Первая строка: {fut["data"][0]}')
                        else:
                            print(f'  Ключи: {keys[:10]}')
                            print(f'  Текст: {r.text[:300]}')
                except json.JSONDecodeError:
                    print(f'  Не JSON: {r.text[:200]}')
        else:
            print(f'  Ошибка ({r.status_code})')
    except Exception as e:
        print(f'{name}: error: {e}')
    print()
