#!/usr/bin/env python3
"""Проверка — можно ли получить live FUTOI с текущей авторизацией"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import config
from datetime import datetime, timedelta

login, pwd = config.load_credentials()

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'X-Requested-With': 'XMLHttpRequest',
})

# Логинимся
r = s.post('https://passport.moex.com/login',
    data={'user[credentials]': login, 'user[password]': pwd},
    headers={'Content-Type': 'application/x-www-form-urlencoded'})
print(f'Login: {r.status_code}')
print(f'Cookies: {[f"{c.name}={c.value[:30]}..." for c in s.cookies]}')

# Пробуем разные даты чтобы понять границу
for days_ago in [0, 1, 5, 10, 14, 15, 20]:
    d = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
    url = f'https://iss.moex.com/iss/analyticalproducts/futoi/securities/GL.csv?iss.meta=off&iss.only=futoi&from={d}&till={d}&latest=0'
    r2 = s.get(url)
    marker = r2.headers.get('X-MicexPassport-Marker', 'N/A')
    first_line = r2.text.split('\\n')[0] if r2.text else 'EMPTY'
    error = 'Invalid date' in r2.text or 'ERROR' in r2.text
    print(f"  {d} ({days_ago:2d} дн.назад): marker={marker} {'❌' if error else '✅'} {first_line[:60]}")
