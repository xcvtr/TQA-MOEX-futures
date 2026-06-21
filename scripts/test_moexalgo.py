#!/usr/bin/env python3
"""Check moexalgo lib for supercandles access"""
import os, sys

with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    for line in f:
        if 'ALGOPACK_APIKEY' in line:
            token = line.strip().split('=', 1)[1].strip()
            break

from moexalgo import session, futures, Market

session.TOKEN = token

# через futures.get
fut = futures.get('SiU6')
methods = [m for m in dir(fut) if not m.startswith('_') and callable(getattr(fut, m, None))]
print('Futures instance methods:', methods)

# через Market
m = Market('FO')
print('\nMarket(FO) methods:', [a for a in dir(m) if not a.startswith('_')])

# попробуем получить tradestats через lib
print('\nSiU6 tradestats sample:')
df = fut.tradestats(start='2026-06-17', end='2026-06-17', use_dataframe=True)
print(f'  columns: {list(df.columns)}')
print(f'  rows: {len(df)}')
print(f'  disb sample: {df["disb"].head(3).tolist() if "disb" in df.columns else "no disb"}')

# попробуем supercandles
print('\nTrying supercandles...')
try:
    sc = fut._prepare_metric('supercandles', 'tradestats', '2026-06-17', '2026-06-17')
    print(f'  got: {sc}')
except Exception as e:
    print(f'  error: {e}')
