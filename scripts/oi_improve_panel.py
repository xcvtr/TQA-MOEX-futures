#!/usr/bin/env python3 -u
"""Панель улучшений OI-портфеля — феномен-тесты на 390K сигналах.

Оси (каждая проверяется отдельно, без подгонки):
1. LONG vs SHORT — асимметрия (классика contrarian: толпа паникует продажами)
2. HOLD — 15/30/45/60/90/120 мин (сейчас 60)
3. Час входа — есть ли часы с лучшим edge
4. Сила сигнала |dn| — сильнее сигнал = сильнее откат?
5. yur vs fiz — сейчас только fiz, может yur/комбинация лучше

Выход: таблицы avg PnL и WR по каждой оси.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR
from datetime import datetime

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

# Собираем все сигналы с forward-профилями на разных горизонтах
# (одна загрузка, несколько fwd)
all_sigs = []
for tk in ['NG', 'BR', 'SV']:
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        try:
            futoi, prices, spec = load(tk, y)
        except Exception:
            continue
        pts, pprc = prices
        for ts in sorted(futoi.keys()):
            dn = futoi[ts]
            if not ((dn <= -THR) or (dn >= THR)):
                continue
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = pprc[idx]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            # forward на 15..120 мин
            fwds = {}
            for hold in [15, 30, 45, 60, 90, 120]:
                j = bisect.bisect_left(pts, ts + hold * 60)
                if j >= len(pts):
                    fwds[hold] = None
                else:
                    fwds[hold] = (pprc[j] - prc) / prc * 100
            direction = 1 if dn <= -THR else -1  # contrarian
            dt_ts = datetime.fromtimestamp(ts - TZ_SHIFT)
            all_sigs.append({
                'tk': tk, 'y': dt_ts.year, 'm': dt_ts.month, 'h': dt_ts.hour,
                'dn': dn, 'dir': direction, 'abs_dn': abs(dn), 'fwds': fwds
            })

print(f"Сигналов: {len(all_sigs)}")

def pnl(sig, hold):
    f = sig['fwds'].get(hold)
    return None if f is None else f * sig['dir']

# ============================================================
print("=" * 70)
print("1. LONG vs SHORT (hold=60)")
print("=" * 70)
for tk in ['NG', 'BR', 'SV']:
    for dname, dval in [('LONG (физ продают)', 1), ('SHORT (физ покупают)', -1)]:
        rows = [s for s in all_sigs if s['tk'] == tk and s['dir'] == dval]
        p = np.array([pnl(s, 60) for s in rows if pnl(s, 60) is not None])
        if len(p) < 30: continue
        print(f"{tk:<4} {dname:<22} n={len(p):>6} avg={p.mean():+.4f}% WR={(p>0).mean()*100:.1f}%")

# ============================================================
print("\n" + "=" * 70)
print("2. HOLD — какой горизонт лучший (по тикерам)")
print("=" * 70)
print(f"{'тикер':<6}" + "".join(f"{h:>10}" for h in [15, 30, 45, 60, 90, 120]))
for tk in ['NG', 'BR', 'SV']:
    rows = [s for s in all_sigs if s['tk'] == tk]
    line = f"{tk:<6}"
    for h in [15, 30, 45, 60, 90, 120]:
        p = np.array([pnl(s, h) for s in rows if pnl(s, h) is not None])
        line += f"{p.mean():>+10.4f}" if len(p) else f"{'—':>10}"
    print(line)
# объединённый портфель
print(f"{'ВСЕ':<6}" + "".join(f"{h:>10}" for h in [15, 30, 45, 60, 90, 120]))
for h in [15, 30, 45, 60, 90, 120]:
    p = np.array([pnl(s, h) for s in all_sigs if pnl(s, h) is not None])
    # это не таблица, выводим строкой ниже

# ============================================================
print("\n" + "=" * 70)
print("3. Час входа (hold=60, все тикеры)")
print("=" * 70)
print(f"{'час':<5}{'n':>7}{'avg%':>9}{'WR%':>7}")
for h in range(7, 24):
    rows = [s for s in all_sigs if s['h'] == h]
    p = np.array([pnl(s, 60) for s in rows if pnl(s, 60) is not None])
    if len(p) < 30: continue
    print(f"{h:>3}:00 {len(p):>7}{p.mean():>+9.4f}{(p>0).mean()*100:>7.1f}")

# ============================================================
print("\n" + "=" * 70)
print("4. Сила сигнала |dn| (hold=60, все тикеры)")
print("=" * 70)
print(f"{'|dn| диапазон':<16}{'n':>7}{'avg%':>9}{'WR%':>7}")
for lo, hi in [(4, 6), (6, 8), (8, 12), (12, 20), (20, 99)]:
    rows = [s for s in all_sigs if lo <= s['abs_dn'] < hi]
    p = np.array([pnl(s, 60) for s in rows if pnl(s, 60) is not None])
    if len(p) < 30: continue
    print(f"{lo}-{hi:<10}{len(p):>7}{p.mean():>+9.4f}{(p>0).mean()*100:>7.1f}")

# ============================================================
print("\n" + "=" * 70)
print("5. (пропущено — день недели требует ts, нет в данных)")
print("=" * 70)

ch.close()
