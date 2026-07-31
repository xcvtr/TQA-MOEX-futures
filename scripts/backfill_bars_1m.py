#!/usr/bin/env python3 -u
"""Backfill PG bars_1m with 1 month of mt5_continuous data."""
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone, timedelta

cutoff = (datetime.now(timezone.utc) - timedelta(days=32)).strftime('%Y-%m-%d')
TICKERS = ['MM','GZ','NG','BR','SV','CR','GD','RN','Si']

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
cur = conn.cursor()
total = 0

for t in TICKERS:
    rows = ch.query(
        "SELECT bt, opn, hi, lo, prc, vol FROM moex.mt5_continuous "
        f"WHERE ticker = '{t}' AND bt >= '{cutoff}' ORDER BY bt"
    ).result_rows
    if not rows: continue
    batch = [(t, r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5]) if r[5] else 0) for r in rows]
    for i in range(0, len(batch), 10000):
        sub = batch[i:i+10000]
        args = ','.join(cur.mogrify('(%s,%s,%s,%s,%s,%s,%s)', x).decode() for x in sub)
        cur.execute('INSERT INTO futures.bars_1m (ticker,bt,opn,hi,lo,prc,vol) VALUES ' + args + ' ON CONFLICT DO NOTHING')
        conn.commit()
    print(f'{t}: {len(batch)} bars', flush=True)
    total += len(batch)

conn.close(); ch.close()
print(f'\nTotal: {total} bars', flush=True)
