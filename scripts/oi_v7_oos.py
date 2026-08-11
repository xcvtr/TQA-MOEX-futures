#!/usr/bin/env python3 -u
"""OOS-проверка финального конфига (risk 12/8%, pyr3, pyra 0.5%, hold 24ч).

1. По годам
2. Walk-forward: трейн 2021-23 → тест 2024-26
3. MC-значимость
4. Средняя сделка: сколько тиков/% зарабатывает
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import scripts.oi_v7_mgmt as v7

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
v7.specs = {}
for t, go, ms, sp, fee in cur.fetchall():
    v7.specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

risk_map = {'BR': 0.12, 'NG': 0.12, 'SV': 0.08, 'RI': 0.08, 'TT': 0.08}
years_all = [2021, 2022, 2023, 2024, 2025, 2026]
sigs = v7.gen_signals(years_all, 8)
print(f"Сигналов 2021-2026: {len(sigs)}")

# 1. По годам
print(f"\n{'год':<6}{'сдел':>6}{'ROI%':>10}{'CashMDD':>9}{'MTM MDD':>9}{'WR%':>7}")
for y in years_all:
    y_sigs = [s for s in sigs if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year == y]
    if not y_sigs: continue
    r = v7.backtest(y_sigs, [y], risk_map, pyr=3, pyra_pct=0.5, horizon_h=24)
    print(f"{y:<6}{r['n']:>6}{r['roi']:>+10.1f}{r['cash_mdd']:>9.1f}{r['mtm_mdd']:>9.1f}{r['wr']:>7.1f}")

# 2. Walk-forward
print(f"\n=== Walk-forward ===")
tr = [s for s in sigs if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year <= 2023]
te = [s for s in sigs if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year >= 2024]
for name, ss, yrs in [("ТРЕЙН 21-23", tr, [2021, 2022, 2023]), ("ТЕСТ 24-26", te, [2024, 2025, 2026])]:
    r = v7.backtest(ss, yrs, risk_map, pyr=3, pyra_pct=0.5, horizon_h=24)
    print(f"{name}: ROI {r['roi']:+.1f}%, CAGR {r['cagr']:.1f}%, CashMDD {r['cash_mdd']:.1f}%, "
          f"MTM {r['mtm_mdd']:.1f}%, WR {r['wr']:.1f}%, {r['n']} сделок")

# 3. Статистика сделок (1 лот, без риска — чистая доходность сигнала)
print(f"\n=== Статистика сигналов (1 лот, движение за 24ч) ===")
print(f"{'тикер':<6}{'сдел':>6}{'avg%':>9}{'med%':>9}{'WR%':>7}{'avg_тик':>9}")
by_tk = {}
for s in sigs:
    by_tk.setdefault(s['tk'], []).append(s)
for tk in sorted(by_tk, key=lambda x: -len(by_tk[x])):
    ss = by_tk[tk]
    ms = ss[0]['ms']
    rets = []
    for s in ss:
        bars = s['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, s['ts']) - 1
        j = bisect.bisect_left(pts, s['ts'] + 24 * 3600)
        if i0 < 0 or j >= len(bars): continue
        rets.append((bars[j,4] - bars[i0,4]) / bars[i0,4] * 100)
    if not rets: continue
    r = np.array(rets)
    print(f"{tk:<6}{len(r):>6}{r.mean():>+9.3f}{np.median(r):>+9.3f}{(r>0).mean()*100:>7.1f}"
          f"{np.mean(r)/100/ms*1000:>9.0f}")

# 4. MC-значимость (пермутация знаков)
print(f"\n=== MC (пермутация знаков сделок) ===")
pnls = []
for s in sigs:
    bars = s['bars']; pts = bars[:, 0]
    ms = s['ms']; sp = s['sp']; fee = s['fee']
    i0 = bisect.bisect_right(pts, s['ts']) - 1
    j = bisect.bisect_left(pts, s['ts'] + 24 * 3600)
    if i0 < 0 or j >= len(bars): continue
    pnl = ((bars[j,4] - bars[i0,4]) / ms * sp - fee * 2)
    pnls.append(pnl)
pnls = np.array(pnls)
rng = np.random.default_rng(42)
real = pnls.sum()
cnt = 0
N = 2000
for i in range(N):
    perm = np.abs(pnls) * rng.choice([-1, 1], size=len(pnls))
    if perm.sum() >= real: cnt += 1
print(f"Сделок: {len(pnls)}, суммарно 1-лот PnL: {real:+,.0f}₽")
print(f"p-value = {cnt/N:.4f}  {'✅ значимо' if cnt/N < 0.05 else '❌ шум'}")
