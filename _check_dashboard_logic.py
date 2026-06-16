#!/usr/bin/env python3
"""Check all tickers with dashboard's actual logic."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

TABLE = 'prices_5m_oi'

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

rows = ch.query(f'SELECT DISTINCT symbol FROM {TABLE}').result_rows
ALL_SYMBOLS = sorted([r[0] for r in rows])

print(f'{"Symbol":>10} | {"Bars":>7} | {"TROUGH→LONG":>12} | {"Troughs":>7} | {"Peaks":>5}')
print('-' * 50)

for symb in ALL_SYMBOLS:
    rows = ch.query(f'''
        SELECT time, yur_buy, yur_sell, total_oi
        FROM {TABLE}
        WHERE symbol = %(t)s AND time >= %(s)s
        ORDER BY time
    ''', parameters={'t': symb, 's': '2025-01-01'}).result_rows

    if len(rows) < 20:
        continue

    # Dashboard logic: yur_net as % of total_oi
    yur_buy = np.array([float(r[1]) for r in rows], dtype=float)
    yur_sell = np.array([float(r[2]) for r in rows], dtype=float)
    total_oi = np.array([float(r[3]) if r[3] > 0 else 1 for r in rows], dtype=float)
    yur_net_pct = (yur_buy - yur_sell) / total_oi * 100

    n = len(yur_net_pct)
    lookback = 12
    min_change = max(2.0, float(np.std(yur_net_pct)) * 0.5)
    
    wave_turns = []
    for i in range(lookback, n - lookback):
        left = yur_net_pct[i-lookback:i]
        if yur_net_pct[i] == max(yur_net_pct[i-lookback:i+lookback]) and yur_net_pct[i] > np.mean(left) + min_change:
            wave_turns.append({'idx': i, 'type': 'PEAK'})
        elif yur_net_pct[i] == min(yur_net_pct[i-lookback:i+lookback]) and yur_net_pct[i] < np.mean(left) - min_change:
            wave_turns.append({'idx': i, 'type': 'TROUGH'})
    
    wave_turns.sort(key=lambda x: x['idx'])
    
    trough_longs = 0
    for i in range(len(wave_turns) - 1):
        t1 = wave_turns[i]
        t2 = wave_turns[i+1]
        if t1['type'] == 'TROUGH' and t2['type'] == 'PEAK' and t2['idx'] - t1['idx'] >= 2:
            trough_longs += 1
    
    all_troughs = sum(1 for w in wave_turns if w['type'] == 'TROUGH')
    all_peaks = sum(1 for w in wave_turns if w['type'] == 'PEAK')

    if trough_longs > 0 or all_troughs > 0:
        print(f'{symb:>10} | {len(rows):>7} | {trough_longs:>12} | {all_troughs:>7} | {all_peaks:>5}')
