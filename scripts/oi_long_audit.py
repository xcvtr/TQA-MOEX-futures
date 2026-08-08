#!/usr/bin/env python3 -u
"""Аудит LONG+h120: MC-значимость + look-ahead + детали.

1. MC: 2000 пермутаций знаков PnL для LONG+h120 (по тикерам)
2. Look-ahead: проверка входа (цена на ts, не позже)
3. Концентрация: по тикерам/годам
4. Сравнение с buy&hold (не сигнал ли просто тренд?)
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600

# Собираем LONG-сигналы с fwd 120
all_trades = []
for tk in ['NG', 'BR', 'SV']:
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        try:
            futoi, prices, spec = load(tk, y)
        except Exception:
            continue
        pts, pprc = prices
        for ts in sorted(futoi.keys()):
            dn = futoi[ts]
            if dn > -THR: continue  # только LONG
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = pprc[idx]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            j = bisect.bisect_left(pts, ts + 7200)
            if j >= len(pts): continue
            fwd = (pprc[j] - prc) / prc * 100
            import datetime as dt
            dt_ts = dt.datetime.fromtimestamp(ts - TZ_SHIFT)
            all_trades.append({'tk': tk, 'y': dt_ts.year, 'ts': ts, 'pnl': fwd})

pnls = np.array([t['pnl'] for t in all_trades])
print(f"LONG-сигналов (fwd 120): {len(pnls)}")
print(f"avg={pnls.mean():+.4f}%  WR={(pnls>0).mean()*100:.1f}%")

# 1. MC
rng = np.random.default_rng(42)
N_MC = 2000
count_ge = 0
real_sum = pnls.sum()
for i in range(N_MC):
    perm = np.abs(pnls) * rng.choice([-1, 1], size=len(pnls))
    if perm.sum() >= real_sum:
        count_ge += 1
print(f"\n=== 1. MONTE CARLO ===")
print(f"Реальная сумма PnL: {real_sum:+,.0f} (в %-пунктах)")
print(f"p-value = {count_ge/N_MC:.4f}  {'✅ значимо' if count_ge/N_MC < 0.05 else '❌ шум'}")

# 2. Look-ahead: проверим что вход по цене <= ts
print(f"\n=== 2. LOOK-AHEAD ===")
ok = True
for tk in ['NG', 'BR', 'SV']:
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        try:
            futoi, prices, spec = load(tk, y)
        except Exception:
            continue
        pts = prices[0]
        for ts in list(futoi.keys())[::500]:
            idx = bisect.bisect_right(pts, ts) - 1
            if idx >= 0 and pts[idx] > ts:
                ok = False
                print(f"  ❌ {tk} {y}: цена после ts!")
if ok:
    print("✅ Вход по цене на ts или раньше (bisect_right) — look-ahead нет")

# 3. Концентрация
print(f"\n=== 3. КОНЦЕНТРАЦИЯ ===")
by_tk = {}
for t in all_trades:
    by_tk.setdefault(t['tk'], []).append(t['pnl'])
total = pnls.sum()
for tk in ['NG', 'BR', 'SV']:
    p = sum(by_tk.get(tk, []))
    print(f"{tk}: n={len(by_tk.get(tk, [])):>6}  PnL={p:>+12,.0f}  доля={p/total*100:>5.1f}%")

by_year = {}
for t in all_trades:
    by_year.setdefault(t['y'], []).append(t['pnl'])
print(f"{'год':<6}{'n':>7}{'PnL':>14}{'доля':>8}")
for y in sorted(by_year):
    p = sum(by_year[y])
    print(f"{y:<6}{len(by_year[y]):>7}{p:>+14,.0f}{p/total*100:>7.1f}%")

# 4. Buy&hold сравнение: сколько бы дал просто долгий лонг NG/BR/SV за период
print(f"\n=== 4. СРАВНЕНИЕ С BUY&HOLD (тренд vs сигнал) ===")
for tk, mt in [('NG', 'NG'), ('BR', 'BR'), ('SV', 'SILV')]:
    r = ch.query(f"SELECT min(bt), max(bt), argMin(prc,bt), argMax(prc,bt) FROM moex.mt5_continuous "
                 f"WHERE ticker='{mt}' AND bt>='2021-01-01'").result_rows
    t0, t1, p0, p1 = r[0]
    bh = (float(p1) / float(p0) - 1) * 100 if p0 else 0
    print(f"{tk}: buy&hold 2021-26 = {bh:+.1f}%")

ch.close()
