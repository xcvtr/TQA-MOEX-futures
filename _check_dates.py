#!/usr/bin/env python3
"""Check last data date per ticker."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

TABLE = 'prices_5m_oi'
TICKERS = ['BR', 'AF', 'SR', 'VB', 'AL', 'LK', 'NM', 'PD', 'IMOEXF', 'Eu', 'Si', 'CR']

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
print(f'{"Ticker":>8} | {"Last data":>20} | {"Total rows":>10}')
print('-' * 45)

for t in TICKERS:
    r = ch.query(f'SELECT max(time), count() FROM {TABLE} WHERE symbol = %(t)s', parameters={'t': t}).result_rows
    if r:
        print(f'{t:>8} | {str(r[0][0]):>20} | {r[0][1]:>10}')
