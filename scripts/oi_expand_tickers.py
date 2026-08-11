#!/usr/bin/env python3 -u
"""Расширение базы: все тикеры с futoi+mt5+specs на thr=5.

Вопрос: какие тикеры дают положительный edge при thr=5?
Проверяем каждый отдельно: сделки/год, WR, avg% за 24ч, вклад по годам.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
DAY_SEC = 86400

# все пары futoi_код -> mt5_ticker (по известным маппингам)
ALL = {
 'BR':'BR', 'NG':'NG', 'SV':'SILV', 'RN':'RN', 'GZ':'GZ', 'Eu':'Eu',
 'RI':'RTSI', 'LK':'LKOH', 'SN':'SNGP', 'SF':'SBRF', 'MG':'MGNT',
 'VB':'VTBR', 'TT':'TATN', 'AF':'AFLT', 'HY':'HYDR',
}

def irk_day(ts):
    return int((ts - 7 * 3600) // DAY_SEC)

def load_tk(fut_tk, mt_tk, y):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur "
                 f"FROM moex.futoi WHERE ticker='{fut_tk}' "
                 f"AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    day_start = {}
    net_map = {}
    for ts, fb, fs, yb, ys in r:
        d = irk_day(ts)
        if d not in day_start:
            day_start[d] = int(fb) - int(fs)
        total = int(fb) + int(fs) + int(yb) + int(ys)
        if total <= 0: continue
        net_map[ts] = (int(fb) - int(fs) - day_start[d]) / total * 100
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc FROM moex.mt5_continuous "
                  f"WHERE ticker='{mt_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, o, h, l, c) for ts, o, h, l, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    return net_map, arr[o]

def analyze_ticker(fut_tk, mt_tk, y, thr=5):
    """Сигналы и их 24ч движение (1 лот). Возвращает список сделок."""
    d = load_tk(fut_tk, mt_tk, y)
    if d is None: return []
    net_map, bars = d
    pts = bars[:, 0]
    day_best = {}
    for ts in sorted(net_map.keys()):
        dn = net_map[ts]
        if dn > -thr: continue
        idx = bisect.bisect_right(pts, ts) - 1
        if idx < 0: continue
        prc = bars[idx, 4]
        if prc <= 0 or (ts - pts[idx]) > 600: continue
        dnum = irk_day(ts)
        if dnum not in day_best or abs(dn) > abs(day_best[dnum]['dn']):
            day_best[dnum] = {'ts': ts, 'prc': prc, 'dn': dn, 'bars': bars}
    out = []
    for dnum, t in day_best.items():
        bars2 = t['bars']
        pts2 = bars2[:, 0]
        i0 = bisect.bisect_right(pts2, t['ts']) - 1
        j = bisect.bisect_left(pts2, t['ts'] + 24*3600)
        if i0 < 0 or j >= len(bars2): continue
        pct = (bars2[j,4] - bars2[i0,4]) / bars2[i0,4] * 100
        out.append({'y': y, 'pct': pct, 'ts': t['ts']})
    return out

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker FROM futures.ticker_specs")
spec_tickers = {x[0] for x in cur.fetchall()}
pg.close()

years = [2022, 2023, 2024, 2025, 2026]
print(f"{'тикер':<6}{'mt5':<7}{'сдел':>6}{'/год':>5}{'avg%':>8}{'WR%':>7}{'2022':>7}{'2023':>7}{'2024':>7}{'2025':>7}{'2026':>7}")
print("-" * 75)
results = {}
for fut_tk, mt_tk in ALL.items():
    if fut_tk not in spec_tickers:
        print(f"{fut_tk:<6}{mt_tk:<7}  (нет specs)")
        continue
    all_t = []
    for y in years:
        all_t.extend(analyze_ticker(fut_tk, mt_tk, y, thr=5))
    if not all_t:
        print(f"{fut_tk:<6}{mt_tk:<7}{'нет данных':>15}")
        continue
    pcts = np.array([t['pct'] for t in all_t])
    # по годам
    by_y = {}
    for t in all_t:
        by_y.setdefault(t['y'], []).append(t['pct'])
    ystr = ""
    for y in years:
        p = np.array(by_y.get(y, []))
        ystr += f"{p.mean() if len(p) else 0:>+7.1f}"
    print(f"{fut_tk:<6}{mt_tk:<7}{len(all_t):>6}{len(all_t)/5:>5.0f}"
          f"{pcts.mean():>+8.2f}{(pcts>0).mean()*100:>7.1f}{ystr}")
    results[fut_tk] = {'n': len(all_t), 'avg': pcts.mean(), 'wr': (pcts>0).mean()*100}
ch.close()
