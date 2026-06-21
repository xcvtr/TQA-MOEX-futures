#!/usr/bin/env python3
"""Оценка объёма Algopack данных и сколько места нужно"""
import os, json, requests, re, sys

with open('/home/user/projects/TQA-MOEX/.env') as f:
    cnt = f.read()
m = re.search(r'ALGOPACK_APIKEY=(.+)', cnt)
TOKEN = m.group(1).strip("'\" \n")
headers = {'Authorization': f'Bearer {TOKEN}'}

# Проверяем через limit=5000 (макс возможный)
# Но сначала смотрим что выдаёт без лимита
for name, url, dt in [
    ('TradeStats', 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2025-06-17&limit=5000', 33),
    ('OBStats', 'https://apim.moex.com/iss/datashop/algopack/fo/obstats.json?date=2025-06-17&limit=5000', 35),
    ('OrderStats', 'https://apim.moex.com/iss/datashop/algopack/fo/orderstats.json?date=2025-06-17&limit=5000', 21),
]:
    r = requests.get(url, headers=headers)
    j = r.json()
    d = j.get('data', {})
    rows = d.get('data', [])
    size_kb = len(r.text) / 1024
    print(f'{name}:')
    print(f'  Строк: {len(rows)} (limit=5000)')
    print(f'  Размер JSON: {size_kb:.1f} KB')
    
    # Сколько тикеров
    tickers = set(rr[2] for rr in rows if len(rr) > dt)
    print(f'  Тикеров: {len(tickers)}')
    
    # Наши ключевые тикеры
    key = {'SiU5', 'BRU5', 'SRU5', 'GDU5', 'SVU5', 'GLDRUBF', 'CNYRUBF', 'IMOEXF', 'CRZ4', 'NGV4'}
    have = key & tickers
    print(f'  Наши тикеры есть: {len(have)}/{len(key)}')
    print()
