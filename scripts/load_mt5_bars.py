#!/usr/bin/env python3
"""Load recent M1 bars from mt5_continuous (CH) into PG bars_1m.
Incremental: only loads last 2 hours, skips existing rows.
Autopurge: keeps 60 days.
"""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import clickhouse_connect as cc
import psycopg2
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

CH_HOST = '10.0.0.60'
CH_PORT = 8123
CH_DB = 'moex'

PG_CONFIG = dict(
    host='10.0.0.60', port=5432, dbname='moex',
    user=os.getenv('MOEX_PG_USER', 'user'),
)

RETENTION_DAYS = 60
TICKERS = ['MM','GZ','NG','BR','SV','CR','GD','RN','Si']  # portfolio tickers

def load_mt5_to_pg():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=2)  # last 2 hours only
    
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    conn = psycopg2.connect(**PG_CONFIG, connect_timeout=10)
    cur = conn.cursor()
    
    total = 0
    for ticker in TICKERS:
        rows = ch.query(f"""
            SELECT bt, opn, hi, lo, prc, vol
            FROM moex.mt5_continuous
            WHERE ticker = '{ticker}' AND bt >= '{cutoff.isoformat()}'
            ORDER BY bt
        """).result_rows
        
        if not rows:
            continue
        
        inserted = 0
        for r in rows:
            bt, opn, hi, lo, prc, vol = r
            try:
                cur.execute("""
                    INSERT INTO futures.bars_1m (ticker, bt, opn, hi, lo, prc, vol)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, bt) DO NOTHING
                """, (ticker, bt, float(opn), float(hi), float(lo), float(prc), int(vol) if vol else 0))
                if cur.rowcount > 0:
                    inserted += 1
            except Exception as e:
                log.warning("Insert error %s/%s: %s", ticker, bt, e)
        
        if inserted:
            log.info("%s: %d new bars", ticker, inserted)
        total += inserted
    
    # Autopurge
    purge_before = now - timedelta(days=RETENTION_DAYS)
    cur.execute("DELETE FROM futures.bars_1m WHERE bt < %s", (purge_before,))
    purged = cur.rowcount
    if purged:
        log.info("Purged %d old bars (< %s)", purged, purge_before.date())
    
    conn.commit()
    cur.close()
    conn.close()
    ch.close()
    log.info("Done: %d new bars loaded", total)

if __name__ == '__main__':
    load_mt5_to_pg()
