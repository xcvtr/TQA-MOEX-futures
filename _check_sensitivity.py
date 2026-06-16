#!/usr/bin/env python3
"""Analyze wave sensitivity for all tickers with different thresholds."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

TABLE = 'prices_5m_oi'
TICKERS = ['BR', 'AF', 'SR', 'VB', 'AL', 'LK', 'NM', 'PD', 'IMOEXF', 'Eu', 'Si', 'CR']

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# Разные настройки
configs = [
    ('std*0.5', lambda std: max(1.0, std * 0.3)),
    ('std*0.5', lambda std: max(1.5, std * 0.5)),
    ('fixed=1.5', lambda std: 1.5),
    ('fixed=1.0', lambda std: 1.0),
    ('fixed=0.7', lambda std: 0.7),
]

for cfg_name, cfg_fn in configs:
    print(f'\n=== min_change = {cfg_name} ===')
    print(f'{"Ticker":>8} | {"TROUGH→LONG":>12} | {"Troughs":>7} | {"Peaks":>5}')
    print('-' * 40)
    
    for t in TICKERS:
        rows = ch.query(f'''
            SELECT time, yur_buy, yur_sell
            FROM {TABLE}
            WHERE symbol = %(t)s AND time >= %(s)s
            ORDER BY time
        ''', parameters={'t': t, 's': '2025-01-01'}).result_rows
        
        if len(rows) < 20:
            continue
        
        yur_net = np.array([float(r[1] - r[2]) for r in rows], dtype=float)
        yur_net = (yur_net - yur_net.mean()) / (yur_net.std() + 1e-10)
        
        n = len(yur_net)
        lookback = 12
        min_change = cfg_fn(float(yur_net.std()))
        
        wave_turns = []
        for i in range(lookback, n - lookback):
            left = yur_net[i-lookback:i]
            if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + min_change:
                wave_turns.append({'idx': i, 'type': 'PEAK'})
            elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - min_change:
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
        print(f'{t:>8} | {trough_longs:>12} | {all_troughs:>7} | {all_peaks:>5}')
