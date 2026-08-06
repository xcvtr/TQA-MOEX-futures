#!/usr/bin/env python3
"""Массовый импорт qsh архива с SMB-шары в CH moex.dom (параллельно).

Импортирует .Quotes.qsh для тикеров BR/NG/SV/RN/EU/Si за диапазон дат.
Использование: python3 scripts/qsh_import_all.py --start 2026-07-01 --end 2026-07-31 --workers 4
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


def parse_qsh_file(path):
    """Распаковать gzip, вернуть кадры [(ts_naive_utc, [(price, type, vol), ...]), ...]."""
    tmp = '/tmp/_qsh_imp.qsh'
    with gzip.open(path, 'rb') as f_in, open(tmp, 'wb') as f_out:
        f_out.write(f_in.read())
    q = QSHParser(tmp)
    q.touch()
    frames = []
    try:
        for data in q:
            ts = data['timestamp']
            if ts.year < 2000:
                continue  # битый кадр (QScalp парсер: год 1)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=MSK)
            ts_utc = ts.astimezone(timezone.utc).replace(tzinfo=None)
            quotes = []
            for qq in data.get('quotes', []):
                price = float(qq['rate'])
                vol = float(qq['volume'])
                if vol > 0:
                    quotes.append((price, 1, vol))
                elif vol < 0:
                    quotes.append((price, 2, -vol))
            frames.append((ts_utc, quotes))
    except (StopIteration, RuntimeError):
        pass
    return frames


def import_day(date_str):
    """Импорт одного дня. Возвращает (дата, тикеров, строк)."""
    day_dir = os.path.join(QSH_DIR, date_str)
    if not os.path.isdir(day_dir):
        return (date_str, 0, 0)
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    total = 0
    tickers = 0
    done = set()
    files = sorted(glob.glob(os.path.join(day_dir, '*.Quotes.qsh')))
    for f in files:
        base = os.path.basename(f)
        sym = base.split('.')[0].upper()
        if not any(sym.startswith(p) for p in PREFIXES):
            continue
        if sym in done:
            continue
        done.add(sym)
        try:
            frames = parse_qsh_file(f)
        except Exception:
            continue
        rows = []
        for ts_utc, quotes in frames:
            for price, typ, vol in quotes:
                rows.append([ts_utc, sym, price, typ, vol])
        for i in range(0, len(rows), BATCH):
            ch.insert('moex.dom_qsh', rows[i:i+BATCH],
                      column_names=['time', 'ticker', 'price', 'type', 'volume'])
        total += len(rows)
        tickers += 1
    ch.close()
    return (date_str, tickers, total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args()

    dates = []
    d = datetime.strptime(args.start, '%Y-%m-%d')
    end = datetime.strptime(args.end, '%Y-%m-%d')
    while d <= end:
        dates.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)

    print(f"Импорт {len(dates)} дней ({args.start}..{args.end}), workers={args.workers}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for date_str, tickers, n in ex.map(import_day, dates):
            print(f"  {date_str}: {tickers} тикеров, {n:,} строк", flush=True)


if __name__ == '__main__':
    main()
