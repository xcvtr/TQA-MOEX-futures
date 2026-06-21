#!/usr/bin/env python3
"""Load fo/tradestats for Si futures - last 14 trading days."""
import urllib.request, json, os, sys, time
from datetime import date, timedelta, datetime
import clickhouse_driver

def read_token():
    paths = [
        os.path.expanduser('~/.hermes/hermes-agent/.env'),
        os.path.expanduser('~/projects/TQA-MOEX-futures/.env'),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    if 'ALGOPACK_APIKEY' in line:
                        return line.split('=', 1)[1].strip().strip("\"'")
    return None

TOKEN = read_token()
if not TOKEN:
    print("No token found")
    sys.exit(1)

CH = clickhouse_driver.Client(host='10.0.0.64')
CH_DB = 'moex_algopack_fo'

CH.execute(f"CREATE DATABASE IF NOT EXISTS {CH_DB}")
CH.execute(f"""
    CREATE TABLE IF NOT EXISTS {CH_DB}.tradestats (
        tradedate Date,
        tradetime String,
        secid String,
        asset_code String,
        pr_open Nullable(Float64),
        pr_high Nullable(Float64),
        pr_low Nullable(Float64),
        pr_close Nullable(Float64),
        pr_std Nullable(Float64),
        vol Nullable(Int64),
        val Nullable(Float64),
        trades Nullable(Int32),
        pr_vwap Nullable(Float64),
        pr_change Nullable(Float64),
        trades_b Nullable(Int32),
        trades_s Nullable(Int32),
        val_b Nullable(Float64),
        val_s Nullable(Float64),
        vol_b Nullable(Int64),
        vol_s Nullable(Int64),
        disb Nullable(Float64),
        pr_vwap_b Nullable(Float64),
        pr_vwap_s Nullable(Float64),
        im Nullable(Float64),
        oi_open Nullable(Int64),
        oi_high Nullable(Int64),
        oi_low Nullable(Int64),
        oi_close Nullable(Int64),
        sec_pr_open Nullable(Int32),
        sec_pr_high Nullable(Int32),
        sec_pr_low Nullable(Int32),
        sec_pr_close Nullable(Int32),
        SYSTIME Nullable(String)
    ) ENGINE = MergeTree()
    ORDER BY (secid, tradedate, tradetime)
""")

COLUMNS = ['tradedate','tradetime','secid','asset_code','pr_open','pr_high','pr_low','pr_close',
           'pr_std','vol','val','trades','pr_vwap','pr_change','trades_b','trades_s',
           'val_b','val_s','vol_b','vol_s','disb','pr_vwap_b','pr_vwap_s',
           'im','oi_open','oi_high','oi_low','oi_close',
           'sec_pr_open','sec_pr_high','sec_pr_low','sec_pr_close','SYSTIME']

# Get Si secids
url = 'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date=2026-06-19&limit=50000'
req = urllib.request.Request(url, headers={'Authorization': f'Bearer {TOKEN}'})
resp = urllib.request.urlopen(req, timeout=60)
data = json.loads(resp.read())
all_rows = data['data']['data']
si_secids = sorted(set(r[2] for r in all_rows if r[3] == 'Si'))
print(f"Si secids: {si_secids}")

today = date.today()
dates = []
d = today - timedelta(days=30)
while d <= today:
    if d.weekday() < 5:
        dates.append(d)
    d += timedelta(days=1)
dates = dates[-14:]

total = 0
t0 = time.time()

for secid in si_secids:
    for day in dates:
        day_str = day.isoformat()
        url = f'https://apim.moex.com/iss/datashop/algopack/fo/tradestats.json?date={day_str}&secid={secid}&limit=50000'
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {TOKEN}'})
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            data = json.loads(resp.read())
            rows = data['data']['data']
        except Exception as e:
            print(f"  {day_str} {secid}: API error {e}")
            continue

        if not rows:
            continue

        records = []
        for row in rows:
            rec = {}
            for i, c in enumerate(COLUMNS):
                v = row[i] if i < len(row) else None
                if v == '' or v == 'None':
                    v = None
                if c == 'tradedate' and isinstance(v, str):
                    v = datetime.strptime(v, '%Y-%m-%d').date()
                rec[c] = v
            records.append(rec)

        try:
            CH.execute(f"INSERT INTO {CH_DB}.tradestats ({', '.join(COLUMNS)}) VALUES", records)
            total += len(records)
            print(f"  {day_str} {secid}: {len(records)} rows OK")
        except Exception as e:
            print(f"  {day_str} {secid}: CH error {e}")

        sys.stdout.flush()

elapsed = time.time() - t0
print(f"\nDONE: {total} rows in {elapsed:.0f}s")

cnt = CH.execute(f"SELECT count() FROM {CH_DB}.tradestats")[0][0]
print(f"Total in CH: {cnt} rows")
disb_cnt = CH.execute(f"SELECT count() FROM {CH_DB}.tradestats WHERE disb IS NOT NULL")[0][0]
print(f"Rows with disb: {disb_cnt}")
