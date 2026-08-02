#!/usr/bin/env python3
"""Копирование futures.dom (PG) → moex.dom (CH) + autopurge PG.

Сборщик dom_api.py (в контейнере mt5-finam) пишет сырые снапшоты стакана в
PG futures.dom (быстрая запись). Чтобы PG не переполнялся:
  - каждые 5 мин копируем новые строки в CH moex.dom (полный архив)
  - раз в день purge: удаляем из PG строки старше 30 дней

Крон: */5 * * * * /home/user/.hermes/scripts/dom_pg_to_ch.sh
"""
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import clickhouse_connect as cc

PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', connect_timeout=5)
CH_HOST, CH_PORT, CH_DB = '10.0.0.60', 8123, 'moex'
PURGE_DAYS = 30
PURGE_MARK = '/tmp/dom_pg_purge_last'

BATCH = 50000


def pg_conn():
    return psycopg2.connect(host=PG['host'], port=PG['port'], dbname=PG['dbname'],
                            user=PG['user'], connect_timeout=PG['connect_timeout'])


def copy_new():
    """Копируем строки PG → CH с ts > max(time) в CH."""
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    r = ch.query("SELECT max(time) FROM moex.dom").result_rows
    last = r[0][0]
    if last is None:
        last = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elif last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ts, ticker, price, side, volume FROM futures.dom
        WHERE ts > %s ORDER BY ts LIMIT %s
    """, (last, BATCH))
    rows = cur.fetchall()
    total = 0
    while rows:
        # CH insert: time, ticker, price, type, volume
        data = []
        for ts, ticker, price, side, volume in rows:
            t = ts.replace(tzinfo=None)  # CH DateTime64(3) naive
            data.append([t, ticker, float(price), int(side), float(volume)])
        ch.insert('moex.dom', data,
                  column_names=['time', 'ticker', 'price', 'type', 'volume'])
        total += len(data)
        if len(rows) < BATCH:
            break
        last = rows[-1][0]
        cur.execute("""
            SELECT ts, ticker, price, side, volume FROM futures.dom
            WHERE ts > %s ORDER BY ts LIMIT %s
        """, (last, BATCH))
        rows = cur.fetchall()
    conn.close()
    ch.close()
    return total


def purge():
    """Раз в день удаляем из PG строки старше PURGE_DAYS."""
    try:
        with open(PURGE_MARK) as f:
            last_purge = datetime.fromisoformat(f.read().strip())
        if datetime.now() - last_purge < timedelta(hours=20):
            return 0
    except Exception:
        pass
    conn = pg_conn()
    cur = conn.cursor()
    cutoff = datetime.now(timezone.utc) - timedelta(days=PURGE_DAYS)
    cur.execute("DELETE FROM futures.dom WHERE ts < %s", (cutoff,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    with open(PURGE_MARK, 'w') as f:
        f.write(datetime.now().isoformat())
    return deleted


def main():
    copied = copy_new()
    purged = purge()
    print(f"copied={copied}, purged={purged}", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
