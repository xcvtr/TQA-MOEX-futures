#!/usr/bin/env python3
"""Fetch pre-SVO D1 prices from MOEX ISS"""
import requests, json, sys

def fetch_full_history(secid, from_date, till_date):
    all_rows = []
    start = 0
    while True:
        url = f'https://iss.moex.com/iss/history/engines/futures/markets/forts/securities/{secid}.json'
        params = {'from': from_date, 'till': till_date, 'start': start, 'iss.meta': 'off'}
        try:
            r = requests.get(url, params=params, timeout=30)
            d = r.json()
            rows = d['history']['data']
            if not rows:
                break
            all_rows.extend(rows)
            start += 100
            if len(rows) < 100:
                break
        except Exception as e:
            print(f'  ERROR at start={start}: {e}', file=sys.stderr)
            break
    return all_rows

# Eu (EUR/RUB) pre-SVO
print("=== Eu PRE-SVO ===")
eu_all = {}
for c in ['EuZ2', 'EuH3', 'EuM3', 'EuU3']:
    rows = fetch_full_history(c, '2021-01-01', '2022-02-28')
    for r in rows:
        dt, close, vol = r[1], r[6], r[9]
        if close is not None and vol is not None and vol > 0:
            if dt not in eu_all or vol > eu_all[dt][1]:
                eu_all[dt] = (float(close), int(vol))
dates = sorted(eu_all.keys())
print(f"  Total days: {len(dates)}")
print(f"  Range: {dates[0]} .. {dates[-1]}")
print(f"  Avg close: {sum(eu_all[d][0] for d in dates)/len(dates):.1f}")

# Si (USDRUB) pre-SVO
print("\n=== Si PRE-SVO ===")
si_all = {}
for c in ['SiZ2', 'SiH3', 'SiM3', 'SiU3']:
    rows = fetch_full_history(c, '2021-01-01', '2022-02-28')
    for r in rows:
        dt, close, vol = r[1], r[6], r[9]
        if close is not None and vol is not None and vol > 0:
            if dt not in si_all or vol > si_all[dt][1]:
                si_all[dt] = (float(close), int(vol))
dates = sorted(si_all.keys())
print(f"  Total days: {len(dates)}")
print(f"  Range: {dates[0]} .. {dates[-1]}")
print(f"  Avg close: {sum(si_all[d][0] for d in dates)/len(dates):.1f}")
