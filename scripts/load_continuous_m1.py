#!/usr/bin/env python3 -u
"""Load JSON files into CH moex.mt5_continuous."""
import json, os, glob, clickhouse_connect as cc

CH = dict(host='10.0.0.60', port=8123, database='moex')
ch = cc.get_client(**CH)

ch.command("""
    CREATE TABLE IF NOT EXISTS moex.mt5_continuous (
        ticker LowCardinality(String),
        bt DateTime,
        opn Float64, hi Float64, lo Float64, prc Float64,
        vol UInt32, tick_vol UInt32
    ) ENGINE = ReplacingMergeTree()
    PARTITION BY toYYYYMM(bt)
    ORDER BY (ticker, bt)
""")

batch_size = 50000
total_all = 0

for fpath in sorted(glob.glob('/tmp/mt5_cont_*.json')):
    ticker = os.path.basename(fpath).replace('mt5_cont_', '').replace('.json', '')
    print(f'Loading {ticker}...', flush=True)
    
    with open(fpath) as f:
        data = json.load(f)
    
    bars = data['bars']
    rows = []
    for b in bars:
        rows.append((
            ticker,
            b['ts'].replace('T', ' ') if 'T' in b['ts'] else b['ts'],
            b['opn'], b['hi'], b['lo'], b['prc'], b['vol'], b['tick_vol']
        ))
    
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i+batch_size]
        ch.insert('moex.mt5_continuous', chunk,
            column_names=['ticker', 'bt', 'opn', 'hi', 'lo', 'prc', 'vol', 'tick_vol'])
        total_all += len(chunk)
        print(f'  {ticker}: {total_all}/{len(rows)}', flush=True)
    
    print(f'  {ticker}: DONE ({len(rows)} bars)', flush=True)

ch.close()
print(f'\nTotal: {total_all} bars loaded into moex.mt5_continuous', flush=True)
