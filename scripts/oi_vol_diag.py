#!/usr/bin/env python3 -u
"""Диагностика: корреляция волатильности с PnL сделки (быстрая версия).

Предвычисляем дневные vol/med один раз на тикер, затем для каждого сигнала
берём значение своего дня через searchsorted.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def daily_vol_med(pts, pprc):
    """Для каждого дня: vol 20д (годовая %) + med 180д. Возвращает (day_ids, vol, med)."""
    day_ids = (pts // 86400).astype(np.int64)
    uniq_days, first_idx, counts = np.unique(day_ids, return_index=True, return_counts=True)
    last_idx = first_idx + counts - 1
    closes = pprc[last_idx]
    rets = np.diff(closes) / closes[:-1]
    nd = len(closes)
    vol = np.full(nd, np.nan)
    med = np.full(nd, np.nan)
    for i in range(20, nd):
        vol[i] = np.std(rets[i - 20:i]) * np.sqrt(252) * 100
    for i in range(30, nd):
        start = max(0, i - 180)
        seg = rets[start:i]
        if len(seg) >= 25:
            nw = len(seg) - 20
            if nw > 0:
                wins = np.array([np.std(seg[j:j + 20]) for j in range(0, nw, 5)])
                med[i] = np.median(wins) * np.sqrt(252) * 100
    return uniq_days, vol, med

all_rows = []
for tk in ['NG', 'BR', 'SV']:
    for y in [2024, 2025, 2026]:
        futoi, prices, spec = load(tk, y)
        pts, pprc = prices
        uniq_days, vol, med = daily_vol_med(pts, pprc)
        fts = sorted(futoi.keys())
        for ts in fts:
            dn = futoi[ts]
            if not ((dn <= -THR) or (dn >= THR)):
                continue
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = pprc[idx]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            j = bisect.bisect_left(pts, ts + 3600)
            if j >= len(pts): continue
            fwd = (pprc[j] - prc) / prc * 100
            direction = 1 if dn <= -THR else -1
            pnl_ret = fwd * direction
            d = int(ts // 86400)
            pos = int(np.searchsorted(uniq_days, d))
            if pos >= len(vol): continue
            v, m = vol[pos], med[pos]
            if np.isnan(v) or np.isnan(m) or m <= 0: continue
            all_rows.append({'tk': tk, 'y': y, 'v': v, 'm': m, 'ratio': v / m, 'pnl': pnl_ret})

print(f"Сделок с vol-метками: {len(all_rows)}")
v = np.array([r['v'] for r in all_rows])
r_ = np.array([r['ratio'] for r in all_rows])
pnl = np.array([r['pnl'] for r in all_rows])

print(f"\nКорреляция v_now vs pnl:    {np.corrcoef(v, pnl)[0,1]:+.3f}")
print(f"Корреляция ratio vs pnl:    {np.corrcoef(r_, pnl)[0,1]:+.3f}")
print(f"Корреляция ratio vs |pnl|:  {np.corrcoef(r_, np.abs(pnl))[0,1]:+.3f}")

print(f"\n{'квантиль ratio':<20}{'n':>6}{'avg_pnl%':>10}{'WR%':>7}")
for lo, hi, label in [(0, 0.5, 'тихо (<0.5)'), (0.5, 0.9, '0.5-0.9'),
                      (0.9, 1.1, 'у медианы'), (1.1, 1.5, '1.1-1.5'),
                      (1.5, 99, 'бурно (>1.5)')]:
    mask = (r_ >= lo) & (r_ < hi)
    if mask.sum() < 10: continue
    seg = pnl[mask]
    print(f"{label:<20}{mask.sum():>6}{seg.mean():>+10.3f}{(seg>0).mean()*100:>7.1f}")

print(f"\n=== SV по годам ===")
for y in [2024, 2025, 2026]:
    rows = [r for r in all_rows if r['tk'] == 'SV' and r['y'] == y]
    if not rows: continue
    p = np.array([r['pnl'] for r in rows])
    rr = np.array([r['ratio'] for r in rows])
    print(f"SV {y}: n={len(rows)} avg_pnl={p.mean():+.3f}% WR={(p>0).mean()*100:.1f}% "
          f"ratio_med={np.median(rr):.2f} corr={np.corrcoef(rr,p)[0,1]:+.3f}")

ch.close()
