#!/usr/bin/env python3
"""Test new Alor token."""
import requests, json, base64, sys
from pathlib import Path

# Get fresh token
env_path = Path('/home/user/projects/TQA-MOEX/.env')
env_vars = {}
for line in env_path.read_text().splitlines():
    if '=' in line:
        k, v = line.split('=', 1)
        env_vars[k.strip()] = v.strip()

cid = env_vars.get('ALOR_Client_ID', '')
cs = env_vars.get('ALOR_Client_Secret', '')
existing = '255375ae-88fa-4f33-bedd-6d9f6a432370'

basic = base64.b64encode(f'{cid}:{cs}'.encode()).decode()
resp = requests.post('https://oauth.alor.ru/token', data={
    'grant_type': 'refresh_token',
    'refresh_token': existing,
    'client_id': cid,
    'client_secret': cs,
}, headers={
    'Authorization': f'Basic {basic}',
    'Content-Type': 'application/x-www-form-urlencoded',
}, timeout=15)

body = resp.json()
token = body.get('access_token', '')
print(f"Token: {token[:40]}...")
print(f"Expires: {body.get('expires_in', '?')}s")
print()

headers = {'Authorization': f'Bearer {token}'}

# 1. History (should work)
resp = requests.get('https://api.alor.ru/md/v2/history', 
    headers=headers, 
    params={'exchange': 'MOEX', 'symbol': 'SMLT', 'tf': 300, 'from': 1748822400, 'to': 1748908800},
    timeout=15)
print(f"1. History: HTTP {resp.status_code}")
if resp.status_code == 200:
    hist = resp.json().get('history', [])
    print(f"   Candles: {len(hist)}")

# 2. Portfolio
resp = requests.post('https://api.alor.ru/md/v2/client/portfolio',
    headers=headers, json={}, timeout=15)
print(f"2. Portfolio: HTTP {resp.status_code}")
if resp.status_code == 200:
    print(f"   {resp.text[:300]}")

# 3. Accounts
for ep in ['https://api.alor.ru/md/v2/client/accounts',
           'https://api.alor.ru/commandapi/v2/client/accounts']:
    resp = requests.get(ep, headers=headers, timeout=15)
    print(f"3. Accounts ({ep.split('/')[-3]}): HTTP {resp.status_code}")
    if resp.status_code == 200:
        print(f"   {resp.text[:300]}")

# 4. Check token type (md vs trade)
# Try to get orders list
resp = requests.get('https://api.alor.ru/commandapi/v2/client/orders',
    headers=headers, params={'portfolio': ''}, timeout=15)
print(f"4. Orders list: HTTP {resp.status_code}")
if resp.status_code == 200:
    print(f"   {resp.text[:300]}")
elif resp.status_code != 404:
    print(f"   {resp.text[:200]}")

# 5. Try risk/limits
resp = requests.get('https://api.alor.ru/md/v2/client/risk',
    headers=headers, timeout=15)
print(f"5. Risk: HTTP {resp.status_code}, {resp.text[:200]}")
