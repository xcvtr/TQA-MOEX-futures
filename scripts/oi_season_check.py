#!/usr/bin/env python3 -u
"""Проверка гипотезы сезонного риска: лето хуже для NG/SV?

1. Средний PnL и WR по месяцам для NG/BR/SV (2021-2026, все сигналы)
2. OOS: сезонный фильтр (лето ×0.5) на 2021-2023 vs 2024-2026
3. Значимость: p-value разницы
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timedelta

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR, HOLD

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

# ============================================================
# 1. PnL по месяцам (forward 60 мин)
# ============================================================
print("=" * 78)
print("1. Средний PnL сигналов по месяцам (2021-2026, forward 60 мин, % )")
print("=" * 78)

all_signals = []  # {tk, y, m, pnl}
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
            j = bisect.bisect_left(pts, ts + 3600)
            if j >= len(pts): continue
            fwd = (pprc[j] - prc) / prc * 100
            direction = 1 if dn <= -THR else -1
            pnl = fwd * direction
            dt_ts = datetime.fromtimestamp(ts - TZ_SHIFT)
            all_signals.append({'tk': tk, 'y': dt_ts.year, 'm': dt_ts.month, 'pnl': pnl})

print(f"Всего сигналов: {len(all_signals)}")
# по тикерам и сезонам
seasons = {'зима (12-2)': [12, 1, 2], 'весна (3-5)': [3, 4, 5],
           'лето (6-8)': [6, 7, 8], 'осень (9-11)': [9, 10, 11]}
print(f"\n{'тикер':<5}{'сезон':<12}{'n':>7}{'avg%':>9}{'WR%':>7}")
print("-" * 42)
for tk in ['NG', 'BR', 'SV']:
    for sname, months in seasons.items():
        rows = [s for s in all_signals if s['tk'] == tk and s['m'] in months]
        if not rows: continue
        p = np.array([r['pnl'] for r in rows])
        print(f"{tk:<5}{sname:<12}{len(p):>7}{p.mean():>+9.3f}{(p>0).mean()*100:>7.1f}")

# помесячно NG/SV
print(f"\nПомесячно (NG+SV, avg%):")
print(f"{'мес':<4}", end="")
for tk in ['NG', 'SV']:
    print(f"{'NG':>8}{'SV':>8}", end="")
print()
for m in range(1, 13):
    print(f"{m:<4}", end="")
    for tk in ['NG', 'SV']:
        rows = [s for s in all_signals if s['tk'] == tk and s['m'] == m]
        if rows:
            p = np.array([r['pnl'] for r in rows])
            print(f"{p.mean():>+8.3f}", end="")
        else:
            print(f"{'—':>8}", end="")
    print()

# ============================================================
# 2. OOS: сезонный фильтр (лето ×0.5) по периодам
# ============================================================
print("\n" + "=" * 78)
print("2. Сезонный фильтр (лето ×0.5 риск для NG/SV) — OOS проверка")
print("=" * 78)

def season_mult(tk, ts):
    m = datetime.fromtimestamp(ts - TZ_SHIFT).month
    if tk in ('NG', 'SV') and m in (6, 7, 8):
        return 0.5
    return 1.0

def sim_signals(signals, period_years, summer_half=True, base_risk=0.05):
    """Простая симуляция: риск base_risk на сделку, реинвест, сезонный множитель."""
    rows = [s for s in signals if s['y'] in period_years]
    eq = 200000.0
    peak = eq
    mdd = 0.0
    for s in rows:
        mult = season_mult(s['tk'], 0) if False else 1.0
        # считаем по месяцу из данных
        m = s['m']
        if summer_half and s['tk'] in ('NG', 'SV') and m in (6, 7, 8):
            mult = 0.5
        pnl_rub = eq * base_risk * mult * (s['pnl'] / 100.0)
        eq += pnl_rub
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    return (eq - 200000) / 200000 * 100, mdd, len(rows)

print(f"\n{'период':<14}{'режим':<18}{'n':>7}{'ROI%':>10}{'MDD%':>8}")
print("-" * 58)
for label, yrs in [('OOS 2021-23', [2021, 2022, 2023]), ('2024-26', [2024, 2025, 2026])]:
    for mode_name, summer_half in [('без фильтра', False), ('лето ×0.5', True)]:
        roi, mdd, n = sim_signals(all_signals, yrs, summer_half)
        print(f"{label:<14}{mode_name:<18}{n:>7}{roi:>+10.1f}{mdd:>8.1f}")

# ============================================================
# 3. Значимость: летние сделки NG/SV хуже остальных?
# ============================================================
print("\n" + "=" * 78)
print("3. Статистика: лето vs остальные месяцы для NG/SV")
print("=" * 78)
for tk in ['NG', 'SV']:
    rows = [s for s in all_signals if s['tk'] == tk]
    summer = np.array([r['pnl'] for r in rows if r['m'] in (6, 7, 8)])
    other = np.array([r['pnl'] for r in rows if r['m'] not in (6, 7, 8)])
    if len(summer) < 30 or len(other) < 30:
        continue
    # t-test (Welch)
    t = (summer.mean() - other.mean()) / np.sqrt(summer.var()/len(summer) + other.var()/len(other))
    print(f"{tk}: лето n={len(summer)} avg={summer.mean():+.3f}% | др.мес n={len(other)} avg={other.mean():+.3f}% | t={t:+.2f}")
    print(f"   разница: {summer.mean()-other.mean():+.3f} п.п. ({'лето ХУЖЕ' if summer.mean() < other.mean() else 'лето ЛУЧШЕ'})")

ch.close()
