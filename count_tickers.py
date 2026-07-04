#!/usr/bin/env python3
"""Check how many distinct tickers AlgoPack tradestats has in a day."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_globals = {}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'algopack_token.py')) as _f:
    exec(_f.read(), _globals)
import moexalgo
moexalgo.session.TOKEN = _globals['TOKEN']

tickers = set()
for r in moexalgo.Market('forts').tradestats(date='2026-07-01', use_dataframe=False):
    t = r.get('asset_code', '')
    if t:
        tickers.add(t)

print(f'Уникальных asset_code за 2026-07-01: {len(tickers)}')
print(f'Список: {sorted(tickers)}')
