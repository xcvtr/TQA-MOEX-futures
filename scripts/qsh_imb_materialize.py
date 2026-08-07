#!/usr/bin/env python3
"""Материализация минутного imbalance стакана (восстановление из дельт dom_qsh).

Для каждого контракта: первый кадр = полный стакан (baseline), дальше дельты
(cumsum). Итог: минутный imbalance (ask-bid)/(ask+bid) → moex.dom_imb_qsh.

Использование: python3 scripts/qsh_imb_materialize.py --workers 6
"""
import sys, os, gzip, glob, argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import clickhouse_connect as cc
import numpy as np

CH_HOST, CH_PORT, CH_DB = '10.0.0.60', 8123, 'moex'
PREFIXES = ('BR', 'NG', 'SV', 'RN')
BATCH = 20000


def materialize_contract(args):
    tkr, = args
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    # читаем ВСЕ строки контракта (может быть много — батчами по дням)
    # сначала дни
    days = ch.query(f"""
        SELECT DISTINCT toDate(time) FROM moex.dom_qsh
        WHERE ticker='{tkr}' AND time > toDateTime64('2000-01-01', 3)
        ORDER BY toDate(time)
    """).result_rows
    out = []
    for (d,) in days:
        rr = ch.query(f"""
            SELECT time, price, type, volume FROM moex.dom_qsh
            WHERE ticker='{tkr}' AND toDate(time)='{d}'
            ORDER BY time
        """).result_rows
        if not rr:
            continue
        # Группируем по кадрам; полные кадры (>30 уровней) → ближние топ-10 от медианы
        frames = defaultdict(list)
        for ts, price, typ, vol in rr:
            frames[ts].append((price, typ, vol))
        imb_min = {}
        for ts, levels in frames.items():
            if len(levels) < 30:
                continue
            bids = [l for l in levels if l[1] == 1]
            asks = [l for l in levels if l[1] == 2]
            if not bids or not asks:
                continue
            best_bid = max(l[0] for l in bids)
            best_ask = min(l[0] for l in asks)
            mid = (best_bid + best_ask) / 2.0
            near = sorted(levels, key=lambda l: abs(l[0]-mid))[:20]
            tb = sum(l[2] for l in near if l[1] == 1)
            ta = sum(l[2] for l in near if l[1] == 2)
            if tb + ta > 0:
                cur_min = ts.replace(second=0, microsecond=0)
                imb_min[cur_min] = (ta - tb) / (ta + tb)
        for m, v in imb_min.items():
            out.append([m, tkr, float(v)])
    # вставка
    for i in range(0, len(out), BATCH):
        ch.insert('moex.dom_imb_qsh', out[i:i+BATCH],
                  column_names=['min', 'ticker', 'imb'])
    ch.close()
    return (tkr, len(days), len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=6)
    args = ap.parse_args()

    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.dom_imb_qsh
        (
            `min` DateTime,
            `ticker` LowCardinality(String),
            `imb` Float64
        )
        ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/1/dom_imb_qsh', '{replica}')
        PARTITION BY toYYYYMM(min)
        ORDER BY (ticker, min)
    """)
    # все контракты наших префиксов
    r = ch.query("""
        SELECT DISTINCT ticker FROM moex.dom_qsh
        WHERE time > toDateTime64('2000-01-01', 3)
          AND (startsWith(ticker,'BR') OR startsWith(ticker,'NG') OR startsWith(ticker,'SV') OR startsWith(ticker,'RN'))
    """).result_rows
    tickers = [x[0] for x in r]
    ch.close()
    print(f"Контрактов: {len(tickers)}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for tkr, days, n in ex.map(materialize_contract, [(t,) for t in tickers]):
            print(f"  {tkr}: {days} дней, {n:,} минут imb", flush=True)


if __name__ == '__main__':
    main()
