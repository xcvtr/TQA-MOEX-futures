#!/home/user/projects/TQA-MOEX-futures/.venv/bin/python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

with open(os.path.join(os.path.dirname(__file__), '..', '..', 'algopack_token.py')) as f:
    for line in f:
        if 'TOKEN' in line and '=' in line:
            TOKEN=*** 1)[1].strip().strip("'").strip('"')
            break

import moexalgo
moexalgo.session.TOKEN*** = list(moexalgo.Market('forts').tradestats(date='2026-06-30', native=True))

# Show tradetime format
for r in data[:5]:
    tt = r.get('tradetime')
    print(f'tradetime={repr(tt)}  type={type(tt).__name__}')
    if isinstance(tt, str):
        print(f'  len={len(tt)} chars={[ord(c) for c in tt[:5]]}')

# Show what the filter fails on
import re
count_all = 0
count_filtered = 0
for r in data:
    td = r.get('tradetime', '')
    td_str = str(td)
    count_all += 1
    # Check if the filter pattern matches
    if len(td_str) >= 5 and td_str[3:5] in ('00', '05', '10', '15', '20', '25', '30', '35', '40', '45', '50', '55'):
        count_filtered += 1

print(f'\nTotal: {count_all}, Matched filter: {count_filtered}')
if count_filtered == 0 and data:
    r = data[0]
    td = str(r.get('tradetime', ''))
    print(f'First tradetime string: \"{td}\" (len={len(td)})')
    print(f'  bytes: {td.encode()[:20]}')
