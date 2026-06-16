#!/usr/bin/env python3
"""Check normed yur_net waves for all tickers — same logic as dashboard."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
import numpy as np
from datetime import datetime, timedelta
from config import CH_HOST, CH_PORT, CH_DB

TABLE = 'prices_5m_oi'
TICKERS = ['BR', 'AF', 'SR', 'VB', 'AL', 'LK', 'NM', 'PD', 'IMOEXF', 'Eu', 'Si', 'CR']

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

print(f'Cutoff: {cutoff}')
print(f'{"Ticker":>8} | {"Bars":>5} | {"TROUGH→LONG":>12} | {"Troughs total":>13} | {"Status":>10}')
print('-' * 65)

for t in TICKERS:
    rows = ch.query(f'''
        SELECT time, yur_buy, yur_sell
        FROM {TABLE}
        WHERE symbol = %(t)s AND time >= %(s)s
        ORDER BY time
    ''', parameters={'t': t, 's': cutoff}).result_rows

    if len(rows) < 12:
        print(f'{t:>8} | {len(rows):>5} | {"—":>12} | {"—":>13} | NO DATA')
        continue

    yur_net = np.array([float(r[1] - r[2]) for r in rows], dtype=float)
    yur_net = (yur_net - yur_net.mean()) / (yur_net.std() + 1e-10)  # z-score norm

    n = len(yur_net)
    lookback = 12
    min_change = max(2.0, float(yur_net.std()) * 0.5)
    
    wave_turns = []
    for i in range(lookback, n - lookback):
        left = yur_net[i-lookback:i]
        if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + min_change:
            wave_turns.append({'idx': i, 'type': 'PEAK', 'val': float(yur_net[i]), 'dir': -1})
        elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - min_change:
            wave_turns.append({'idx': i, 'type': 'TROUGH', 'val': float(yur_net[i]), 'dir': 1})
    
    wave_turns.sort(key=lambda x: x['idx'])
    
    trough_longs = 0
    for i in range(len(wave_turns) - 1):
        t1 = wave_turns[i]
        t2 = wave_turns[i+1]
        if t1['type'] == 'TROUGH' and t2['type'] == 'PEAK' and t2['idx'] - t1['idx'] >= 2:
            trough_longs += 1
    
    all_troughs = sum(1 for w in wave_turns if w['type'] == 'TROUGH')
    status = '✅ ENTRIES' if trough_longs > 0 else '—'
    print(f'{t:>8} | {len(rows):>5} | {trough_longs:>12} | {all_troughs:>13} | {status}')
