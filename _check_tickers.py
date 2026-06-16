#!/usr/bin/env python3
"""Check which tickers have OI data and TROUGH→LONG entries."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
from datetime import datetime, timedelta
from config import CH_HOST, CH_PORT, CH_DB

TABLE = 'prices_5m_oi'
TICKERS = ['BR', 'AF', 'SR', 'VB', 'AL', 'LK', 'NM', 'PD', 'IMOEXF', 'Eu', 'Si', 'CR']

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
print(f'Cutoff: {cutoff}')
print()

for t in TICKERS:
    rows = ch.query(f'''
        SELECT time, yur_buy, yur_sell
        FROM {TABLE}
        WHERE symbol = %(t)s AND time >= %(s)s
        ORDER BY time
    ''', parameters={'t': t, 's': cutoff}).result_rows

    if len(rows) < 5:
        print(f'{t}: only {len(rows)} rows — no data')
        continue

    yur_net = [float(r[1] - r[2]) for r in rows]
    
    # TROUGH detection: local min where val < -0.3 and previous > -0.3
    trough_longs = []
    for i in range(2, len(yur_net)-1):
        if yur_net[i] < yur_net[i-1] and yur_net[i] <= yur_net[i+1]:
            val_before = yur_net[i-1]
            if val_before > -0.3 and yur_net[i] < -0.3:
                trough_longs.append({
                    'time': rows[i][0].strftime('%m-%d %H:%M'),
                    'yur_net': round(yur_net[i], 2),
                    'prev': round(val_before, 2)
                })
    
    print(f'{t}: {len(rows)} rows, {len(trough_longs)} TROUGH→LONG entries')
    if trough_longs:
        for tr in trough_longs[-5:]:
            print(f'  {tr["time"]}: yur_net {tr["yur_net"]} (prev {tr["prev"]})')
    
    last_yur = [(r[0].strftime('%m-%d %H:%M'), round(yur_net[i], 2)) for i, r in enumerate(rows[-5:])]
    print(f'  Last yur_net: {last_yur}')
    print()
