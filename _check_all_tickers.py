#!/usr/bin/env python3
"""Check ALL tickers in moex_oi table for TROUGH→LONG entries."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

TABLE = 'prices_5m_oi'

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# Получаем все ticker'ы из таблицы
rows = ch.query(f'SELECT DISTINCT symbol FROM {TABLE}').result_rows
ALL_SYMBOLS = sorted([r[0] for r in rows])
print(f'Total unique symbols in table: {len(ALL_SYMBOLS)}')
print('Symbols:', ', '.join(ALL_SYMBOLS))
print()

print(f'{"Symbol":>10} | {"Bars":>7} | {"TROUGH→LONG":>12} | {"Troughs":>7} | {"Peaks":>5} | {"Period":>16}')
print('-' * 65)

for symb in ALL_SYMBOLS:
    rows = ch.query(f'''
        SELECT time, yur_buy, yur_sell
        FROM {TABLE}
        WHERE symbol = %(t)s AND time >= %(s)s
        ORDER BY time
    ''', parameters={'t': symb, 's': '2025-01-01'}).result_rows

    if len(rows) < 20:
        continue

    yur_net = np.array([float(r[1] - r[2]) for r in rows], dtype=float)
    yur_net = (yur_net - yur_net.mean()) / (yur_net.std() + 1e-10)

    n = len(yur_net)
    lookback = 12
    min_change = max(2.0, float(yur_net.std()) * 0.5)
    
    troughs = 0
    peaks = 0
    for i in range(lookback, n - lookback):
        left = yur_net[i-lookback:i]
        if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + min_change:
            peaks += 1
        elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - min_change:
            troughs += 1
    
    first = rows[0][0].strftime('%Y-%m')
    last = rows[-1][0].strftime('%Y-%m-%d')
    
    if troughs > 0 or peaks > 0:
        print(f'{symb:>10} | {len(rows):>7} | {troughs:>12} | {troughs:>7} | {peaks:>5} | {first}–{last}')
