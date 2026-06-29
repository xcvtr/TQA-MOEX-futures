#!/home/user/projects/TQA-MOEX-futures/.venv/bin/python3
"""Debug AlgoPack API key."""
import os, json, urllib.request, urllib.error

with open(os.path.join(os.path.dirname(__file__), '.env')) as f:
    for line in f:
        if 'ALGOPACK_APIKEY' in line and '=' in line:
            key = line.split('=', 1)[1].strip().strip('"').strip("'")
            break

print(f"Key length: {len(key)}")
print(f"Key ends: ...{key[-10:]}")

url = 'https://iss.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2026-06-26&start=0'
req = urllib.request.Request(url, headers={
    'Authorization': f'Bearer {key}',
    'User-Agent': 'Mozilla/5.0'
})

try:
    resp = urllib.request.urlopen(req, timeout=30)
    body = resp.read().decode()
    print(f"Status: {resp.status}")
    print(f"Content-Type: {resp.headers.get('Content-Type')}")
    print(f"Body (first 500): {body[:500]}")
    data = json.loads(body)
    cols = data.get('tradestats', {}).get('columns', [])
    rows = data.get('tradestats', {}).get('data', [])
    print(f"Columns: {cols[:15]}")
    print(f"Rows: {len(rows)}")
    if rows:
        print(f"First: {rows[0]}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
    body = e.read().decode()
    print(f"Body: {body[:300]}")
except Exception as e:
    print(f"Error: {e}")
