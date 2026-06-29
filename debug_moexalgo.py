#!/home/user/projects/TQA-MOEX-futures/.venv/bin/python3
"""Debug moexalgo request URL."""
import os

with open(os.path.join(os.path.dirname(__file__), '.env')) as f:
    for line in f:
        if 'ALGOPACK_APIKEY' in line and '=' in line:
            key = line.split('=', 1)[1].strip().strip('"').strip("'")
            break

import moexalgo
moexalgo.session.TOKEN=*** requests
old = requests.Session.request

def debug(method, url, *a, **kw):
    print(f"URL: {method} {url}")
    h = kw.get('headers', {})
    if 'Authorization' in h:
        print(f"  Auth: {h['Authorization'][:40]}...")
    return old(method, url, *a, **kw)

requests.Session.request = debug

from datetime import datetime
today = datetime.now().strftime('%Y-%m-%d')

try:
    list(moexalgo.Market('forts').tradestats(date=today, native=True))
except Exception as e:
    print(f"Error: {e}")
