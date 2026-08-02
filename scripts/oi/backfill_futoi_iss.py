#!/usr/bin/env python3
"""Докачка истории futoi (май-июль 2026) с бесплатного ISS.

ISS отдаёт историю для дат старше 14 дней от сегодня, окнами по ~3 дня
(1000 строк на тикер). Формат: securities/{TICKER}.json?from&till (без 'd').

Скрипт идёт от 2026-07-18 (граница 14 дней) назад до start_date,
окнами по 3 дня, и пишет в CH moex.futoi (ReplicatedReplacingMergeTree).

bt в CH = MSK + 8 (Asia/Irkutsk). ISS отдаёт MSK.
"""
import sys, os, json, time, argparse
from datetime import datetime, timedelta

import clickhouse_connect as cc
import requests

CH_HOST = os.getenv('CH_HOST', '10.0.0.60')
BASE = 'https://iss.moex.com/iss/analyticalproducts/futoi/securities'

def fetch_day_window(ticker, start, end):
    """Запросить окно [start, end] (даты ISO), вернуть список записей."""
    url = f"{BASE}/{ticker}.json"
    params = {
        'iss.meta': 'off',
        'iss.only': 'futoi',
        'from': start,
        'till': end,
    }
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            d = r.json()
            data = d.get('futoi', {}).get('data', [])
            if data and isinstance(data[0], list) and 'Invalid date' in str(data[0][0]):
                return None  # даты в 14-дневном окне
            return data
        except Exception as e:
            if attempt == 2:
                print(f"  ERR {ticker} {start}..{end}: {e}", flush=True)
                return []
            time.sleep(3)
    return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tickers', default='BR,NG')
    ap.add_argument('--start', default='2026-05-01', help='начало периода (включительно)')
    ap.add_argument('--end', default='2026-07-18', help='конец периода (граница 14 дней)')
    ap.add_argument('--window', type=int, default=3, help='дней в окне')
    ap.add_argument('--pause', type=float, default=0.5, help='пауза между запросами')
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(',') if t.strip()]
    ch = cc.get_client(host=CH_HOST, port=8123, database='moex')

    # Окна: от end назад до start
    end_d = datetime.strptime(args.end, '%Y-%m-%d')
    start_d = datetime.strptime(args.start, '%Y-%m-%d')
    windows = []
    cur_end = end_d
    while cur_end >= start_d:
        cur_start = cur_end - timedelta(days=args.window - 1)
        if cur_start < start_d:
            cur_start = start_d
        windows.append((cur_start.strftime('%Y-%m-%d'), cur_end.strftime('%Y-%m-%d')))
        cur_end = cur_start - timedelta(days=1)
    print(f"Окон: {len(windows)} для {tickers}", flush=True)

    total_inserted = 0
    for ticker in tickers:
        for (ws, we) in windows:
            data = fetch_day_window(ticker, ws, we)
            if data is None:
                print(f"  {ticker} {ws}..{we}: в 14-дневном окне (skip)", flush=True)
                continue
            if not data:
                continue
            rows = []
            by_key = {}
            for rec in data:
                if len(rec) < 13 or not isinstance(rec[2], str):
                    continue
                # col: 2=tradedate, 3=tradetime, 4=ticker, 5=clgroup, 7=pos_long, 8=pos_short
                tdate, ttime, tk, clg = rec[2], rec[3], rec[4], rec[5]
                pos_long = rec[7]
                pos_short = rec[8]
                if pos_long is None or pos_short is None:
                    continue
                # bt = MSK datetime + 8ч → IRK
                msk = datetime.strptime(f"{tdate} {ttime}", '%Y-%m-%d %H:%M:%S')
                bt = msk + timedelta(hours=8)
                key = (tk, bt)
                if key not in by_key:
                    by_key[key] = [tk, bt, 0, 0, 0, 0]
                row = by_key[key]
                if clg == 'FIZ':
                    row[2] = int(pos_long)
                    row[3] = abs(int(pos_short))
                elif clg == 'YUR':
                    row[4] = int(pos_long)
                    row[5] = abs(int(pos_short))
            rows = list(by_key.values())
            if rows:
                ch.insert('moex.futoi', rows, column_names=['ticker', 'bt', 'buy_fiz', 'sell_fiz', 'buy_yur', 'sell_yur'])
                total_inserted += len(rows)
                print(f"  {ticker} {ws}..{we}: +{len(rows)} (total {total_inserted})", flush=True)
            time.sleep(args.pause)
    print(f"\nГотово. Вставлено записей: {total_inserted}", flush=True)
    ch.close()

if __name__ == '__main__':
    main()
