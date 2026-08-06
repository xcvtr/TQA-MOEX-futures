#!/usr/bin/env python3
"""Импорт QScalp .qsh (Quotes) в CH moex.dom.

Формат .qsh: gzip → QScalp History Data → QSHParser (LEB128).
Кадры: timestamp (MSK) + quotes [{rate, volume}] — инкрементальные дельты стакана:
  volume > 0 → bid (выставление/увеличение), volume < 0 → ask.
В CH: time (UTC), ticker (контракт), price, type (1=bid, 2=ask), volume (abs).

Использование: python3 scripts/qsh_to_ch.py --dir data/qsh --date 2026-01-25
"""
import sys
import os
import gzip
import glob
import argparse
from datetime import datetime, timedelta, timezone

import clickhouse_connect as cc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'qsh_parser-master'))
from qsh_parser import QSHParser  # noqa: E402

CH_HOST, CH_PORT, CH_DB = '10.0.0.60', 8123, 'moex'
MSK = timezone(timedelta(hours=3))
BATCH = 50000


def parse_qsh(path):
    """Распаковать gzip и вернуть кадры [(ts_utc, [(price, type, vol), ...]), ...]."""
    tmp = '/tmp/_qsh_tmp.qsh'
    with gzip.open(path, 'rb') as f_in, open(tmp, 'wb') as f_out:
        f_out.write(f_in.read())
    q = QSHParser(tmp)
    q.touch()
    frames = []
    try:
        for data in q:
            ts = data['timestamp']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=MSK)
            ts_utc = ts.astimezone(timezone.utc).replace(tzinfo=None)
            quotes = []
            for qq in data.get('quotes', []):
                price = float(qq['rate'])
                vol = float(qq['volume'])
                if vol > 0:
                    quotes.append((price, 1, vol))   # bid
                elif vol < 0:
                    quotes.append((price, 2, -vol))  # ask
            frames.append((ts_utc, quotes))
    except (StopIteration, RuntimeError):
        pass  # нормальный конец файла
    return frames


def import_frames(ticker, frames, ch):
    rows = []
    for ts_utc, quotes in frames:
        for price, typ, vol in quotes:
            rows.append([ts_utc, ticker, price, typ, vol])
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i+BATCH]
        ch.insert('moex.dom', chunk,
                  column_names=['time', 'ticker', 'price', 'type', 'volume'])
        total += len(chunk)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/qsh')
    ap.add_argument('--date', required=True, help='YYYY-MM-DD')
    ap.add_argument('--tickers', default='BR,NG,SV,RN,SI', help='префиксы контрактов через запятую')
    args = ap.parse_args()

    day_dir = os.path.join(args.dir, args.date)
    prefixes = [p.strip().upper() for p in args.tickers.split(',')]
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

    files = sorted(glob.glob(os.path.join(day_dir, '*.Quotes.qsh')))
    total_all = 0
    done = set()
    for f in files:
        base = os.path.basename(f)
        sym = base.split('.')[0].upper()
        if not any(sym.startswith(p) for p in prefixes):
            continue
        if sym in done:
            continue
        done.add(sym)
        frames = parse_qsh(f)
        n = import_frames(sym, frames, ch)
        total_all += n
        print(f"  {sym}: {len(frames)} кадров, {n} строк", flush=True)

    print(f"ИТОГО: {total_all} строк в CH moex.dom (день {args.date})", flush=True)
    ch.close()


if __name__ == '__main__':
    main()
