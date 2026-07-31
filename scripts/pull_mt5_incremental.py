#!/usr/bin/env python3 -u
"""Incremental M1 data pull from FINAM MT5 into CH moex.mt5_continuous."""
import sys, subprocess, json
from datetime import datetime

CONT_SYMBOLS = {
    'Si': 'ALLFUTSi', 'GD': 'ALLFUTGOLD', 'MM': 'MOEXMM',
    'RN': 'ALLFUTROSN', 'NG': 'ALLFUTNG', 'BR': 'ALLFUTBR',
}

PY_SCRIPT = '''
import MetaTrader5 as mt5, clickhouse_connect as cc
from datetime import datetime

mt5.initialize()
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

for ticker, sym in %%SYMBOLS%%:
    rows = ch.query(f"SELECT max(bt) FROM moex.mt5_continuous WHERE ticker='{ticker}'").result_rows
    last_dt = rows[0][0] if rows and rows[0][0] else datetime(2020,1,1)
    
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, last_dt, datetime.now())
    if rates is None or len(rates) <= 1:
        print(f'{ticker}: no new data (last={last_dt})')
        continue
    
    new_rates = [r for r in rates if datetime.fromtimestamp(r[0]) > last_dt]
    if not new_rates:
        print(f'{ticker}: up to date')
        continue
    
    batch = [(ticker, datetime.fromtimestamp(r[0]), float(r[1]), float(r[2]),
              float(r[3]), float(r[4]), int(r[5]), int(r[6])) for r in new_rates]
    ch.insert('moex.mt5_continuous', batch,
              column_names=['ticker','bt','opn','hi','lo','prc','vol','tick_vol'])
    print(f'{ticker}: {len(batch)} bars ({batch[0][1]} -> {batch[-1][1]})')

ch.close()
mt5.shutdown()
print('DONE')
'''

# Kill excess MT5 terminals first
subprocess.run(['pkill', '-f', 'terminal64.exe'], timeout=5)
import time; time.sleep(2)

# Run via wine
script = PY_SCRIPT.replace('%%SYMBOLS%%', json.dumps(list(CONT_SYMBOLS.items())))
result = subprocess.run(['wine', 'python', '-c', script], capture_output=True, text=True, timeout=120)
print(result.stdout)
if result.stderr:
    for line in result.stderr.split('\n'):
        if 'fixme' not in line and line.strip():
            print(f'ERR: {line}')
