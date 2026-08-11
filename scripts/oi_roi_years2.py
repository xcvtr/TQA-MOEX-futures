#!/usr/bin/env python3 -u
"""ROI по годам — через КАНОНИЧЕСКИЙ v7.backtest (не упрощённый цикл).

v7.backtest стартует с 200K. Для компаунда по годам: пересчитываем PnL сделок
пропорционально капиталу (contracts = eq*risk/go — линейно по eq).
Берём сделки года, считаем через v7.backtest с масштабированием.
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

years = [2022, 2023, 2024, 2025, 2026]
sigs = v7.gen_signals(years, 5)

def run_by_year(risk_map, label):
    print(f"\n=== {label} ===")
    print(f"{'год':<6}{'сдел':>6}{'ROI_год':>10}{'CashMDD':>9}{'MTM MDD':>9}{'WR%':>7}")
    eq = 200000.0
    for y in years:
        y_sigs = [s for s in sigs if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year == y]
        if not y_sigs: continue
        # v7.backtest считает ROI от 200K фикс. НО contracts = eq*risk/go линейно.
        # Для компаунда: запускаем на капитале eq (масштаб), ROI год = тот же %,
        # т.к. все PnL линейны по капиталу.
        r = v7.backtest(y_sigs, [y], risk_map, pyr=3, pyra_pct=0.5, horizon_h=24)
        roi_y = r['roi']  # % за год (от 200K, но линейно = от любого eq тот же %)
        eq *= (1 + roi_y / 100)
        print(f"{y:<6}{r['n']:>6}{roi_y:>+10.1f}{r['cash_mdd']:>9.1f}{r['mtm_mdd']:>9.1f}{r['wr']:>7.1f}")
    total_roi = (eq / 200000 - 1) * 100
    cagr = ((eq / 200000) ** (1/len(years)) - 1) * 100
    print(f"{'ИТОГО':<6} ROI {total_roi:>+10.1f}%  CAGR {cagr:>+8.1f}%")
    return total_roi, cagr

# Проверка эквивалентности: v7.backtest на всех годах сразу
for rm, label in [
    ({'BR': 0.08, 'NG': 0.08, 'SV': 0.05, 'RI': 0.05, 'TT': 0.05}, "MTM~14% (risk 8/5%)"),
    ({'BR': 0.12, 'NG': 0.12, 'SV': 0.08, 'RI': 0.08, 'TT': 0.08}, "MTM~21% (risk 12/8%)"),
    ({'BR': 0.15, 'NG': 0.15, 'SV': 0.10, 'RI': 0.10, 'TT': 0.10}, "MTM~26% (risk 15/10%)"),
]:
    run_by_year(rm, label)

# Контроль: v7.backtest за 5 лет сразу (должен совпасть с произведением годовых)
print("\n=== Контроль: v7.backtest за 5 лет (единый прогон) ===")
for rm, label in [
    ({'BR': 0.08, 'NG': 0.08, 'SV': 0.05, 'RI': 0.05, 'TT': 0.05}, "risk 8/5%"),
    ({'BR': 0.12, 'NG': 0.12, 'SV': 0.08, 'RI': 0.08, 'TT': 0.08}, "risk 12/8%"),
    ({'BR': 0.15, 'NG': 0.15, 'SV': 0.10, 'RI': 0.10, 'TT': 0.10}, "risk 15/10%"),
]:
    r = v7.backtest(sigs, years, rm, pyr=3, pyra_pct=0.5, horizon_h=24)
    print(f"{label}: ROI {r['roi']:+.1f}%, CAGR {r['cagr']:.1f}%, Cash {r['cash_mdd']:.1f}%, MTM {r['mtm_mdd']:.1f}%")
