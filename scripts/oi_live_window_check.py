#!/usr/bin/env python3 -u
"""Сверка live vs тестер: влияние временного окна.

Бэктест (triz_oi_backtest.py и мои verify): торгует ВСЕ futoi ts (7:00-23:59 MSK).
Live (paper_trader): market_open = 15:00-04:50 IRK = 10:00-23:50 MSK.

Вопрос: сколько LONG-сигналов live пропускает (7:00-9:59 MSK) и их вклад в PnL?
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600  # futoi MSK → IRK

# Дневная сессия live: 15:00-04:50 IRK = 10:00 MSK открытие
# Утренние бары futoi: 7:00-9:59 MSK
rows = []
for tk in ['NG', 'BR', 'SV']:
    for y in [2024, 2025, 2026]:
        try:
            futoi, prices, spec = load(tk, y)
        except Exception:
            continue
        pts, pprc = prices
        for ts in sorted(futoi.keys()):
            dn = futoi[ts]
            if dn > -THR: continue  # LONG сигнал
            import datetime as dt
            msk = dt.datetime.fromtimestamp(ts - TZ_SHIFT)
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = pprc[idx]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            j = bisect.bisect_left(pts, ts + 7200)
            if j >= len(pts): continue
            fwd = (pprc[j] - prc) / prc * 100
            is_morning = msk.hour < 10  # 7:00-9:59 MSK — вне окна live
            rows.append({'tk': tk, 'y': y, 'msk_h': msk.hour, 'morning': is_morning, 'pnl': fwd})

pnl_all = np.array([r['pnl'] for r in rows])
pnl_live = np.array([r['pnl'] for r in rows if not r['morning']])
pnl_morning = np.array([r['pnl'] for r in rows if r['morning']])

print(f"Всего LONG: {len(pnl_all)}  avg={pnl_all.mean():+.4f}%")
print(f"Окно live (10:00+ MSK): {len(pnl_live)}  avg={pnl_live.mean():+.4f}%  WR={(pnl_live>0).mean()*100:.1f}%")
print(f"Утро (7:00-9:59 MSK, вне live): {len(pnl_morning)}  avg={pnl_morning.mean():+.4f}%  WR={(pnl_morning>0).mean()*100:.1f}%")

# Вклад утра в суммарный PnL
print(f"\nДоля утренних сигналов: {len(pnl_morning)/len(pnl_all)*100:.1f}%")
print(f"Вклад утра в суммарный PnL: {pnl_morning.sum()/pnl_all.sum()*100:.1f}%")

# Помесячно часы
print(f"\n{'час MSK':<8}{'n':>7}{'avg%':>9}{'WR%':>7}")
for h in range(7, 24):
    seg = np.array([r['pnl'] for r in rows if r['msk_h'] == h])
    if len(seg) < 10: continue
    print(f"{h:>4}:00 {len(seg):>7}{seg.mean():>+9.4f}{(seg>0).mean()*100:>7.1f}")

ch.close()
