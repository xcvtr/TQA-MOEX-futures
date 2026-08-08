#!/usr/bin/env python3 -u
"""Феномен: когда происходит отскок после накопленной паники?

Сигнал: накопленный day_net <= -thr (физ панически продали за день).
Вопросы:
1. В какой час дня сигнал появляется (когда входить)?
2. Куда идёт цена после сигнала: 1ч/2ч/4ч/до конца дня/след.день открытие/след.день close/2 дня?
3. На каком горизонте максимальный отскок?

Никаких модельных допущений — только статистика движения.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

VALID = {'BR':'BR', 'NG':'NG', 'SV':'SILV', 'RN':'RN', 'GZ':'GZ', 'Eu':'Eu',
         'RI':'RTSI', 'LK':'LKOH', 'SN':'SNGP', 'SP':'SBRF', 'MG':'MGNT',
         'VB':'VTBR', 'TT':'TATN', 'AF':'AFLT', 'HY':'HYDR'}

def load_tk(fut_tk, mt_tk, y):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch.query(f"SELECT bt, buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi "
                 f"WHERE ticker='{fut_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    day_start = {}
    net_map = {}
    for bt, fb, fs, yb, ys in r:
        ts = bt.replace(tzinfo=None).timestamp() + TZ_SHIFT
        d = int((ts - TZ_SHIFT) // 86400)
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

def analyze(fut_tk, mt_tk, y, thr=8):
    d = load_tk(fut_tk, mt_tk, y)
    if d is None: return []
    net_map, bars = d
    pts = bars[:, 0]
    out = []
    # для каждого дня: последний бар, где dn <= -thr (момент максимальной паники)
    day_signals = {}
    for ts in sorted(net_map.keys()):
        dn = net_map[ts]
        if dn > -thr: continue
        day = int((ts - TZ_SHIFT)//86400)
        # храним ПОСЛЕДНИЙ (по времени) сигнал дня
        if day not in day_signals or ts > day_signals[day]['ts']:
            day_signals[day] = {'ts': ts, 'dn': dn}
    for day, s in day_signals.items():
        ts = s['ts']
        idx = bisect.bisect_right(pts, ts) - 1
        if idx < 0: continue
        prc = bars[idx, 4]
        if prc <= 0 or (ts - pts[idx]) > 600: continue
        msk_h = datetime.fromtimestamp(ts - TZ_SHIFT).hour
        row = {'tk': fut_tk, 'y': y, 'h': msk_h, 'prc': prc, 'ts': ts, 'dn': s['dn']}
        # движение на горизонтах (в %)
        horizons = {'1ч': 3600, '2ч': 7200, '4ч': 14400}
        for hname, hsec in horizons.items():
            j = bisect.bisect_left(pts, ts + hsec)
            if j < len(bars):
                row[hname] = (bars[j, 4] - prc) / prc * 100
            else:
                row[hname] = None
        # до конца дня (последний бар дня)
        day_end = int((ts - TZ_SHIFT)//86400) + 1
        cutoff = day_end * 86400 - TZ_SHIFT
        j = bisect.bisect_right(pts, cutoff) - 1
        if j > idx:
            row['EOD'] = (bars[j, 4] - prc) / prc * 100
        else:
            row['EOD'] = None
        # следующий день: open, close
        next_start = day_end * 86400 - TZ_SHIFT
        j = bisect.bisect_left(pts, next_start)
        if j < len(bars):
            row['D1_open'] = (bars[j, 1] - prc) / prc * 100
            # close следующего дня
            next_end = (day_end + 1) * 86400 - TZ_SHIFT
            j2 = bisect.bisect_right(pts, next_end) - 1
            if j2 > j:
                row['D1_close'] = (bars[j2, 4] - prc) / prc * 100
            else:
                row['D1_close'] = None
        else:
            row['D1_open'] = None; row['D1_close'] = None
        out.append(row)
    return out

# собираем по всем тикерам/годам
all_rows = []
for fut_tk, mt_tk in VALID.items():
    for y in [2022, 2023, 2024, 2025, 2026]:
        all_rows.extend(analyze(fut_tk, mt_tk, y))

print(f"Сигналов: {len(all_rows)}")
print(f"\n{'горизонт':<12}{'n':>7}{'avg%':>9}{'WR%':>7}{'медиана%':>9}")
print("-" * 48)
for h in ['1ч', '2ч', '4ч', 'EOD', 'D1_open', 'D1_close']:
    vals = [r[h] for r in all_rows if r.get(h) is not None]
    if not vals: continue
    v = np.array(vals)
    print(f"{h:<12}{len(v):>7}{v.mean()*100:>+9.3f}{(v>0).mean()*100:>7.1f}{np.median(v)*100:>+9.3f}")

print(f"\n=== Час формирования сигнала ===")
print(f"{'час МСК':<10}{'n':>7}{'avg EOD%':>10}{'WR EOD%':>8}")
for h in range(10, 24):
    rows = [r for r in all_rows if r['h'] == h and r.get('EOD') is not None]
    if len(rows) < 5: continue
    v = np.array([r['EOD'] for r in rows])
    print(f"{h:>4}:00 {len(rows):>7}{v.mean()*100:>+10.3f}{(v>0).mean()*100:>8.1f}")

print(f"\n=== По тикерам (EOD) ===")
print(f"{'тикер':<6}{'n':>6}{'avg EOD%':>10}{'WR%':>7}")
for tk in sorted(set(r['tk'] for r in all_rows)):
    rows = [r for r in all_rows if r['tk'] == tk and r.get('EOD') is not None]
    if len(rows) < 5: continue
    v = np.array([r['EOD'] for r in rows])
    print(f"{tk:<6}{len(v):>6}{v.mean()*100:>+10.3f}{(v>0).mean()*100:>7.1f}")
ch.close()
