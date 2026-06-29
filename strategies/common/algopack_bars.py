#!/usr/bin/env python3
"""AlgoPack bars loader — reads token from algopack_token.py"""
import os, sys, logging
from datetime import datetime, date, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from config import CH_HOST, CH_PORT, CH_DB, DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('algopack_bars')

# Import token from gitignored file
_token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'algopack_token.py')
exec(open(_token_path).read())

import moexalgo
moexalgo.session.TOKEN = TOKEN
import clickhouse_connect as cc

def get_ch():
    return cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def short_ticker(secid):
    s = secid.rstrip('0123456789')
    return s[:-1] if len(s) > 1 else s

def get_portfolio():
    import psycopg2
    pg = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cur = pg.cursor()
    cur.execute("SELECT DISTINCT ticker FROM futures.portfolio WHERE enabled=true")
    tickers = {r[0] for r in cur.fetchall()}
    cur.close()
    pg.close()
    return tickers

def ensure_tables():
    ch = get_ch()
    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.bars (
            ticker String, bt DateTime,
            opn Float64, hi Float64, lo Float64, prc Float64,
            vol UInt32, vol_b UInt32, vol_s UInt32, oi UInt32
        ) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/1/bars', '{replica}')
        PARTITION BY toYYYYMM(bt) ORDER BY (ticker, bt)
    """)
    ch.close()

def load_date(target_date):
    log.info("Loading %s ...", target_date)
    portfolio = get_portfolio()
    ensure_tables()
    dt_str = target_date.strftime('%Y-%m-%d')
    try:
        raw = list(moexalgo.Market('forts').tradestats(date=dt_str, native=True))
    except Exception as e:
        log.error("Fetch failed: %s", e)
        return 0
    log.info("  %d raw rows", len(raw))
    groups = defaultdict(list)
    for r in raw:
        t = short_ticker(r.get('ticker', ''))
        if t and r.get('tradedate'):
            groups[t].append(r)
    ch = get_ch()
    import psycopg2
    pg = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    pcur = pg.cursor()
    total = 0
    for ticker, rows in groups.items():
        ch_rows = []
        for r in rows:
            td = r.get('tradetime', '')
            dd = r.get('tradedate')
            if not td or not dd:
                continue
            if isinstance(dd, str):
                dd = datetime.strptime(dd, '%Y-%m-%d').date()
            if isinstance(td, str):
                bt = datetime.combine(dd, datetime.strptime(td, '%H:%M:%S').time())
            else:
                bt = td
            ch_rows.append((
                ticker, bt,
                float(r.get('pr_open', 0) or 0),
                float(r.get('pr_high', 0) or 0),
                float(r.get('pr_low', 0) or 0),
                float(r.get('pr_close', 0) or 0),
                int(r.get('vol', 0) or 0),
                int(r.get('vol_b', 0) or 0),
                int(r.get('vol_s', 0) or 0),
                int(r.get('oi_close', 0) or 0),
            ))
        if not ch_rows:
            continue
        try:
            ch.insert('moex.bars', ch_rows, column_names=[
                'ticker','bt','opn','hi','lo','prc','vol','vol_b','vol_s','oi'])
        except Exception as e:
            log.warning("CH fail %s: %s", ticker, e)
        if ticker in portfolio:
            try:
                pg_rows = [r[:] for r in ch_rows]  # same columns, same order
                execute_values(pcur,
                    "INSERT INTO futures.prices (ticker,bt,opn,hi,lo,prc,vol,vol_b,vol_s,oi) VALUES %s ON CONFLICT DO NOTHING",
                    pg_rows)
                pcur.execute("DELETE FROM futures.prices WHERE bt < now() - INTERVAL '2 months'")
                pg.commit()
            except Exception as e:
                log.warning("PG fail %s: %s", ticker, e)
        total += len(ch_rows)
    ch.close()
    pcur.close()
    pg.close()
    log.info("  %d bars saved", total)
    return total

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--load-date', type=str)
    parser.add_argument('--backfill', type=int)
    args = parser.parse_args()
    if args.dry_run:
        log.info("Imports OK")
        sys.exit(0)
    if args.load_date:
        load_date(datetime.strptime(args.load_date, '%Y-%m-%d').date())
    elif args.backfill:
        for i in range(args.backfill):
            d = date.today() - timedelta(days=i)
            if d.weekday() < 5:
                load_date(d)
    else:
        load_date(date.today())
