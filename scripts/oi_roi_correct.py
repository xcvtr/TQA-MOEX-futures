#!/usr/bin/env python3 -u
"""ПРАВИЛЬНЫЙ ROI по годам: единый прогон, eq фиксируется на конец года.

v7.backtest считает CAGR от eq_final за все годы — это верно.
Здесь: модифицируем — логируем eq на конец каждого года в ЕДИНОМ прогоне.
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

def backtest_with_years(sigs, years, risk_map, slip=1, pyr=3, pyra_pct=0.5, horizon_h=24):
    """Копия v7.backtest + возврат eq_по_годам."""
    eq = 200000.0; peak_cash = eq; peak_mtm = eq
    cash_mdd = mtm_mdd = 0.0
    n = 0; wins = 0
    eq_by_year = {}
    for t in sorted(sigs, key=lambda x: x['ts']):
        y = datetime.fromtimestamp(t['ts'], tz=timezone.utc).year
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['ts']) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip
        risk = risk_map.get(t['tk'], 0.08)
        base_lots = max(1, int(eq * risk / go))
        parts = [(base_lots, fill_p)]
        i_max = bisect.bisect_right(pts, t['ts'] + horizon_h * 3600)
        for k in range(1, pyr):
            level = fill_p * (1 + k * pyra_pct / 100)
            found = False
            for bi in range(i0, min(i_max, len(bars))):
                if bars[bi, 2] >= level:
                    parts.append((base_lots, bars[bi, 2] + ms * slip))
                    found = True
                    break
            if not found: break
        j = bisect.bisect_left(pts, t['ts'] + 24 * 3600)
        if j >= len(bars): continue
        exit_p = bars[j, 4] - ms * slip
        pnl = 0.0
        for lots, p_in in parts:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak_cash = max(peak_cash, eq)
        cash_mdd = max(cash_mdd, (peak_cash - eq) / peak_cash * 100)
        for bi in range(i0, min(j, len(bars))):
            lo = bars[bi, 3]
            mtm_pnl = 0.0
            for lots, p_in in parts:
                mtm_pnl += ((lo - p_in) / ms * sp - fee * 2) * lots
            mtm_eq = (eq - pnl) + mtm_pnl
            peak_mtm = max(peak_mtm, mtm_eq)
            mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100 if peak_mtm > 0 else 0)
        # eq на конец года (после сделки — если сделка последняя в году, запишем)
        eq_by_year[y] = eq
    return eq, eq_by_year, cash_mdd, mtm_mdd, n, wins

years = [2022, 2023, 2024, 2025, 2026]
sigs = v7.gen_signals(years, 5)
# сортировка по ts
sigs_sorted = sorted(sigs, key=lambda x: x['ts'])

for rm, label in [
    ({'BR': 0.08, 'NG': 0.08, 'SV': 0.05, 'RI': 0.05, 'TT': 0.05}, "MTM~14% (risk 8/5%)"),
    ({'BR': 0.12, 'NG': 0.12, 'SV': 0.08, 'RI': 0.08, 'TT': 0.08}, "MTM~21% (risk 12/8%)"),
    ({'BR': 0.15, 'NG': 0.15, 'SV': 0.10, 'RI': 0.10, 'TT': 0.10}, "MTM~26% (risk 15/10%)"),
]:
    eq_final, eq_by_year, cash_mdd, mtm_mdd, n, wins = backtest_with_years(
        sigs_sorted, years, rm, pyr=3, pyra_pct=0.5, horizon_h=24)
    print(f"\n=== {label} ===")
    print(f"{'год':<6}{'eq_конец':>12}{'ROI_год':>10}{'CAGR_год':>10}")
    prev = 200000.0
    for y in years:
        eq_y = eq_by_year.get(y, prev)
        roi_y = (eq_y / prev - 1) * 100
        print(f"{y:<6}{eq_y:>12,.0f}{roi_y:>+10.1f}{'':>10}")
        prev = eq_y
    total_roi = (eq_final / 200000 - 1) * 100
    cagr = ((eq_final / 200000) ** (1/len(years)) - 1) * 100
    print(f"{'ИТОГО':<6}{eq_final:>12,.0f}{total_roi:>+10.1f}{cagr:>+10.1f}")
    print(f"CashMDD {cash_mdd:.1f}%, MTM MDD {mtm_mdd:.1f}%, WR {wins/n*100:.1f}%, {n} сделок")
