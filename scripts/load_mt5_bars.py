#!/usr/bin/env python3
"""Load recent M1 bars into PG bars_1m.
Primary: mt5_continuous (FINAM, CH)
Fallback: prices_5min (ISS snapshots, CH)
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
TICKERS = ['MM','GZ','NG','BR','SV','CR','GD','RN','Si']

def load_bars():
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
    
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    conn = psycopg2.connect(**PG_CONFIG, connect_timeout=10)
    cur = conn.cursor()
    total = 0
    
    for ticker in TICKERS:
        rows = None
        
        # Source 1: mt5_continuous (FINAM, indicative continuous)
        try:
            q = ("SELECT bt, opn, hi, lo, prc, vol FROM moex.mt5_continuous "
                 f"WHERE ticker = '{ticker}' AND bt >= '{cutoff}' ORDER BY bt")
            rows = ch.query(q).result_rows
            if rows:
                log.info("%s: %d bars from mt5_continuous", ticker, len(rows))
        except Exception as e:
            log.warning("%s mt5_continuous error: %s", ticker, e)
        
        # Source 2: prices_5min (ISS snapshots, fallback)
        if not rows:
            try:
                # ISS data is 5-min snapshots, spread across M1
                q = ("SELECT bt, opn, hi, lo, prc, vol FROM moex.prices_5min "
                     f"WHERE ticker = '{ticker}' AND bt >= '{cutoff}' ORDER BY bt")
                rows = ch.query(q).result_rows
                if rows:
                    log.info("%s: %d bars from prices_5min (fallback)", ticker, len(rows))
            except Exception as e:
                log.warning("%s prices_5min error: %s", ticker, e)
        
        if not rows:
            continue
        
        batch = [(ticker, r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                  int(r[5]) if r[5] else 0) for r in rows]
        
        for i in range(0, len(batch), 10000):
            sub = batch[i:i+10000]
            args = ','.join(cur.mogrify('(%s,%s,%s,%s,%s,%s,%s)', x).decode() for x in sub)
            cur.execute('INSERT INTO futures.bars_1m (ticker,bt,opn,hi,lo,prc,vol) VALUES ' +
                        args + ' ON CONFLICT DO NOTHING')
            conn.commit()
        total += len(batch)
    
    # Autopurge
    try:
        purge_before = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).strftime('%Y-%m-%d')
        cur.execute("DELETE FROM futures.bars_1m WHERE bt < %s::date", (purge_before,))
        purged = cur.rowcount
        if purged:
            log.info("Purged %d old bars (< %s)", purged, purge_before)
    except Exception as e:
        log.warning("Autopurge failed (non-fatal): %s", e)
    
    conn.commit()
    cur.close()
    conn.close()
    ch.close()
    log.info("Done: %d new bars loaded", total)

if __name__ == '__main__':
    load_bars()
