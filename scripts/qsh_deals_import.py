#!/usr/bin/env python3
"""Импорт QScalp .qsh Deals (сделки с агрессором) в CH moex.deals_qsh.

Deals формат (QSHParser): trade_type ASK/BID, exchange_date_time (MSK),
transaction_price, transaction_volume, open_interest.
  ASK = агрессивная ПОКУПКА (type=2), BID = агрессивная ПРОДАЖА (type=1).

Параллельный импорт. Фильтр битых кадров (year < 2000).
Использование: python3 scripts/qsh_deals_import.py --start 2026-01-05 --end 2026-07-31 --workers 6
"""
import os
import sys
import gzip
import glob
import argparse
from datetime import datetime, timedelta, timezone
from concurrent.futures import ProcessPoolExecutor

import clickhouse_connect as cc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'qsh_parser-master'))
from qsh_parser import QSHParser  # noqa: E402

CH_HOST, CH_PORT, CH_DB = '10.0.0.60', 8123, 'moex'
MSK = timezone(timedelta(hours=3))
BATCH = 50000
PREFIXES = ('BR', 'NG', 'SV', 'RN', 'EU', 'SI')
QSH_DIR = '/mnt/qsh'


def parse_deals_file(path):
    """Распаковать gzip, вернуть [(ts_naive_utc, price, type, volume, oi), ...]."""
    tmp = '/tmp/_qsh_deals.qsh'
    with gzip.open(path, 'rb') as f_in, open(tmp, 'wb') as f_out:
        f_out.write(f_in.read())
    q = QSHParser(tmp)
    q.touch()
    rows = []
    try:
        for data in q:
            ts = data.get('exchange_date_time')
            if ts is None:
                continue
            if ts.year < 2000:
                continue  # битый кадр
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=MSK)
            ts_utc = ts.astimezone(timezone.utc).replace(tzinfo=None)
            tt = data.get('trade_type', '')
            typ = 2 if tt == 'ASK' else 1 if tt == 'BID' else 0
            if typ == 0:
                continue
            price = float(data.get('transaction_price', 0))
            vol = float(data.get('transaction_volume', 0))
            oi = float(data.get('open_interest', 0))
            if vol <= 0:
                continue
            rows.append([ts_utc, price, typ, vol, oi])
    except (StopIteration, RuntimeError):
        pass
    return rows


def import_day(date_str):
    day_dir = os.path.join(QSH_DIR, date_str)
    if not os.path.isdir(day_dir):
        return (date_str, 0, 0)
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    total = 0
    tickers = 0
    done = set()
    files = sorted(glob.glob(os.path.join(day_dir, '*.Deals.qsh')))
    for f in files:
        base = os.path.basename(f)
        sym = base.split('.')[0].upper()
        if not any(sym.startswith(p) for p in PREFIXES):
            continue
        if sym in done:
            continue
        done.add(sym)
        try:
            rows = parse_deals_file(f)
        except Exception:
            continue
        # prefix ticker: BR → 'BRD', NG → 'NGD' (Deals отдельная таблица)
        out = []
        for ts_utc, price, typ, vol, oi in rows:
            out.append([ts_utc, sym, price, typ, vol, oi])
        for i in range(0, len(out), BATCH):
            ch.insert('moex.deals_qsh', out[i:i+BATCH],
                      column_names=['time', 'ticker', 'price', 'type', 'volume', 'oi'])
        total += len(out)
        tickers += 1
    ch.close()
    return (date_str, tickers, total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--workers', type=int, default=6)
    args = ap.parse_args()

    dates = []
    d = datetime.strptime(args.start, '%Y-%m-%d')
    end = datetime.strptime(args.end, '%Y-%m-%d')
    while d <= end:
        dates.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)

    print(f"Импорт Deals {len(dates)} дней, workers={args.workers}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for date_str, tickers, n in ex.map(import_day, dates):
            print(f"  {date_str}: {tickers} тикеров, {n:,} сделок", flush=True)


if __name__ == '__main__':
    main()
