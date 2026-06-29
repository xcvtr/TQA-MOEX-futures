#!/home/user/projects/TQA-MOEX-futures/.venv/bin/python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Read token
token = ""
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'algopack_token.py')) as f:
    for line in f:
        if 'TOKEN' in line and '=' in line:
            token = line.split('=', 1)[1].strip().strip("'").strip('"')
            break
print(f'Token: {token[:15]}...{token[-5:]}')

import moexalgo
moexalgo.session.TOKEN*** import httpx

from datetime import datetime
today = datetime.now().strftime('%Y-%m-%d')

try:
    data = list(moexalgo.Market('forts').tradestats(date=today, native=True))
    print(f'Rows: {len(data)}')
    if data:
        for col in ['secid','time','open','high','low','close','volume','oi','vol_b','vol_s']:
            print(f'  {col}: {data[0].get(col)}')
except Exception as e:
    print(f'Error: {e}')
