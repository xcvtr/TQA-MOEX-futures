#!/usr/bin/env python3
import urllib.request, json, os, sys

# Read token
found = False
with open(os.path.expanduser('~/projects/TQA-MOEX-futures/.env')) as f:
    for line in f:
        if 'ALGOPACK_APIKEY' in line:
            TOKEN = line.split('=', 1)[1].strip().strip("\"'")
            found = True
            break
if not found:
    print("No token found")
    sys.exit(1)

url = 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2026-06-19&limit=20000'
req = urllib.request.Request(url, headers={'Authorization': f'Bearer {TOKEN}'})
resp = urllib.request.urlopen(req, timeout=60)
data = json.loads(resp.read())
rows = data['data']['data']

si_secids = set()
for r in rows:
    if r[3] == 'Si':
        si_secids.add(r[2])
print(f"Si secids on 2026-06-19: {sorted(si_secids)}")

si_rows = [r for r in rows if r[3] == 'Si']
print(f"Total Si rows: {len(si_rows)}")
if si_rows:
    print(f"Column sample: date={si_rows[0][0]} time={si_rows[0][1]} secid={si_rows[0][2]} close={si_rows[0][7]} disb={si_rows[0][20]}")
    # Check all secids
    secids = set(r[2] for r in si_rows)
    print(f"Active Si secids in data: {sorted(secids)}")
    for secid in sorted(secids):
        srows = [r for r in si_rows if r[2] == secid]
        print(f"  {secid}: {len(srows)} rows, times {srows[0][1]}-{srows[-1][1]}")
