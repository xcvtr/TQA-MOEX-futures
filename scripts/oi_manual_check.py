#!/usr/bin/env python3 -u
"""Ручная проверка 3 сигналов: точные бары вокруг сигнала."""
import sys, bisect
import numpy as np
import clickhouse_connect as cc
from datetime import datetime

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

# BR, найдём сигнал с day_net <= -8 в 2024
r = ch.query("SELECT bt, buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi "
             "WHERE ticker='BR' AND bt>='2024-01-01' AND bt<='2024-12-31'").result_rows
day_start = {}
rows = []
for bt, fb, fs, yb, ys in r:
    ts = bt.replace(tzinfo=None).timestamp() + TZ_SHIFT
    d = int((ts - TZ_SHIFT) // 86400)
    if d not in day_start:
        day_start[d] = int(fb) - int(fs)
    total = int(fb) + int(fs) + int(yb) + int(ys)
    if total <= 0: continue
    dn = (int(fb) - int(fs) - day_start[d]) / total * 100
    rows.append((ts, dn, bt))

# последние сигналы
sig_rows = [(ts, dn, bt) for ts, dn, bt in rows if dn <= -8]
print(f"BR 2024: сигналов day_net<=-8: {len(sig_rows)}")
for ts, dn, bt in sig_rows[-3:]:
    print(f"  сигнал: {bt} (MSK) ts={ts} dn={dn:.1f}%")

# цены BR вокруг последнего сигнала
ts, dn, bt = sig_rows[-1]
r2 = ch.query(f"SELECT bt, opn, hi, lo, prc FROM moex.mt5_continuous "
              f"WHERE ticker='BR' AND bt>='2024-12-01' AND bt<='2025-01-15'").result_rows
arr = np.array([(t.replace(tzinfo=None).timestamp() + 0, o, h, l, c) for t, o, h, l, c in r2], dtype=np.float64)
# mt5_continuous время — какая TZ? Проверим
print(f"\nБар на момент сигнала (ts={ts}, MSK {datetime.fromtimestamp(ts-TZ_SHIFT)}):")
idx = bisect.bisect_right(arr[:, 0], ts) - 1
print(f"  ближайший бар: {datetime.fromtimestamp(arr[idx,0])} (raw ts={arr[idx,0]})  "
      f"close={arr[idx,4]}")
# печатаем бары вокруг
print(f"\nБары вокруг сигнала (bt в raw):")
for i in range(max(0, idx-2), min(len(arr), idx+30)):
    dt = datetime.fromtimestamp(arr[i,0])
    print(f"  {dt}  o={arr[i,1]:.2f} h={arr[i,2]:.2f} l={arr[i,3]:.2f} c={arr[i,4]:.2f}")
ch.close()
