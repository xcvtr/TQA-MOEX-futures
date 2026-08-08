#!/usr/bin/env python3 -u
"""OOS-проверка v5: по годам + walk-forward (train/test split)."""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import scripts.oi_v5 as v5

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
specs = {}
for t, go, ms, sp, fee in cur.fetchall():
    specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()
v5.specs = specs  # monkeypatch: specs для модуля

years_all = [2021, 2022, 2023, 2024, 2025, 2026]
sigs = v5.gen_signals(years_all, 8)
print(f"Сигналов 2021-2026: {len(sigs)}")
risk_map = {'BR': 0.20, 'NG': 0.20, 'SV': 0.15, 'RI': 0.15, 'TT': 0.15}

# 1. По годам (отдельный бэктест на год, капитал 200K каждый)
print(f"\n{'год':<6}{'сдел':>6}{'ROI%':>10}{'MDD%':>8}{'WR%':>7}")
for y in years_all:
    y_sigs = [s for s in sigs if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year == y]
    if not y_sigs: continue
    r = v5.backtest(y_sigs, [y], risk_map, pyr=3, pyra_pct=0.5)
    print(f"{y:<6}{r['n']:>6}{r['roi']:>+10.1f}{r['mdd']:>8.1f}{r['wr']:>7.1f}")

# 2. Walk-forward: train 2021-23 (параметры НЕ меняем, фикс), test 2024-26
print(f"\n=== Walk-forward: трейн 2021-23, тест 2024-26 ===")
tr = [s for s in sigs if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year <= 2023]
te = [s for s in sigs if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year >= 2024]
for name, ss, yrs in [("ТРЕЙН 21-23", tr, [2021, 2022, 2023]), ("ТЕСТ 24-26", te, [2024, 2025, 2026])]:
    r = v5.backtest(ss, yrs, risk_map, pyr=3, pyra_pct=0.5)
    print(f"{name}: ROI {r['roi']:+.1f}%, MDD {r['mdd']:.1f}%, WR {r['wr']:.1f}%, {r['n']} сделок")

# 3. Чувствительность pyra_pct: 0.3-1.0 (на тесте 24-26)
print(f"\n=== Чувствительность pyra_pct (тест 24-26) ===")
for pp in [0.3, 0.5, 0.7, 1.0, 1.5]:
    r = v5.backtest(te, [2024, 2025, 2026], risk_map, pyr=3, pyra_pct=pp)
    print(f"pyra_pct={pp:.1f}: ROI {r['roi']:+.1f}%, MDD {r['mdd']:.1f}%, CAGR {r['cagr']:.1f}%")
