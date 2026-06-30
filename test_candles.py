#!/home/user/projects/TQA-MOEX-futures/.venv/bin/python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# Read token from file
with open(os.path.join(os.path.dirname(__file__), '..', '..', 'algopack_token.py')) as f:
    for line in f:
        if 'TOKEN' in line and '=' in line:
            TOKEN=*** 1)[1].strip().strip("'").strip('"')
            break

import moexalgo
moexalgo.session.TOKEN=*** = list(moexalgo.Market('forts').candles(date='2026-06-30', native=True))
print(f'Candles: {len(data)} rows')
if data:
    cols = list(data[0].keys())
    print(f'Columns: {cols}')
    si = [r for r in data if r.get('ticker','').startswith('Si')]
    print(f'Si: {len(si)} rows')
    if si:
        for r in si[:2]:
            print(f'  {r.get(\"tradedate\")} {r.get(\"tradetime\")} O={r.get(\"open\")} H={r.get(\"high\")} L={r.get(\"low\")} C={r.get(\"close\")} V={r.get(\"volume\")}')
