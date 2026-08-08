#!/usr/bin/env python3 -u
"""Просадки vs волатильность: распределение PnL по квантилям vol.

Гипотеза: даже если средний PnL одинаков, в волатильные периоды
дисперсия/хвосты больше → серии убытков → MDD. Тогда фильтр
«не торговать при экстремальной волатильности» может снизить просадку.

Считаем по сигналам (как oi_vol_diag): vol/med в момент сигнала,
распределение forward PnL за 60 мин, худшие хвосты, серии убытков.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def daily_vol_med(pts, pprc):
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
        for ts in sorted(futoi.keys()):
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
            pnl = fwd * direction
            d = int(ts // 86400)
            pos = int(np.searchsorted(uniq_days, d))
            if pos >= len(vol): continue
            v, m = vol[pos], med[pos]
            if np.isnan(v) or np.isnan(m) or m <= 0: continue
            all_rows.append({'tk': tk, 'y': y, 'ratio': v / m, 'pnl': pnl, 'v': v})

print(f"Сигналов: {len(all_rows)}")
r_ = np.array([r['ratio'] for r in all_rows])
pnl = np.array([r['pnl'] for r in all_rows])

print(f"\n{'квантиль ratio':<22}{'n':>7}{'avg%':>8}{'std%':>8}{'p05%':>8}{'p95%':>8}{'maxLoss%':>10}{'skew':>7}")
print("-" * 78)
for lo, hi, label in [(0, 0.5, 'тихо (<0.5)'), (0.5, 0.9, '0.5-0.9'),
                      (0.9, 1.1, 'у медианы'), (1.1, 1.5, '1.1-1.5'),
                      (1.5, 2.0, '1.5-2.0'), (2.0, 99, 'экстрим (>2)')]:
    mask = (r_ >= lo) & (r_ < hi)
    if mask.sum() < 10: continue
    seg = pnl[mask]
    print(f"{label:<22}{mask.sum():>7}{seg.mean():>+8.3f}{seg.std():>8.3f}"
          f"{np.percentile(seg,5):>+8.3f}{np.percentile(seg,95):>+8.3f}"
          f"{seg.min():>+10.3f}{(np.mean(seg**3))/seg.std()**3:>7.2f}")

# Моделирование: портфель с фильтром "не торговать при ratio > X"
# Смотрим MDD по эквити при разных потолках волатильности
print(f"\n=== Симуляция: потолок волатильности (не входить при ratio > cap) ===")
print(f"{'cap':>6}{'n':>8}{'ROI%':>10}{'MDD%':>8}")
for cap in [99, 3.0, 2.0, 1.5, 1.2, 1.0, 0.8]:
    mask = r_ < cap
    seg = pnl[mask]
    if len(seg) < 100: continue
    # простая симуляция: 200K, риск 5% от капитала на сделку, реинвест
    eq = 200000.0
    peak = eq
    mdd = 0.0
    for p in seg:
        # p в %: риск 5% капитала, pnl_rub = eq * 0.05 * (p/100)
        pnl_rub = eq * 0.05 * (p / 100.0)
        eq += pnl_rub
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    roi = (eq - 200000) / 200000 * 100
    print(f"{cap:>6}{len(seg):>8}{roi:>+10.1f}{mdd:>8.1f}")

ch.close()
