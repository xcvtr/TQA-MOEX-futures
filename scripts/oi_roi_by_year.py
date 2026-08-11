#!/usr/bin/env python3 -u
"""ROI и CAGR по годам для уровней MTM 15/20/25%.

Точный расчёт: капитал компаундится через год, ROI_год = (конец_года/начало_года - 1)*100.
CAGR = ((конец/начало)^(1/N) - 1)*100.
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
sigs_all = v7.gen_signals(years, 5)

def run_by_year(risk_map, label):
    print(f"\n=== {label} ===")
    print(f"{'год':<6}{'сдел':>6}{'ROI_год':>10}{'ROI_комп':>12}{'CAGR_год':>10}{'CashMDD':>9}{'MTM':>8}{'WR%':>7}")
    eq = 200000.0
    for y in years:
        y_sigs = [s for s in sigs_all if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year == y]
        if not y_sigs:
            continue
        r = v7.backtest(y_sigs, [y], risk_map, pyr=3, pyra_pct=0.5, horizon_h=24)
        # r['roi'] считался от 200K фикс. Пересчитаем от текущего eq:
        # r['roi'] = (eq_конец - 200000)/200000*100 при старте 200K в этом году
        # но компаунд требует старт от eq. r считает от 200K — масштабируем линейно:
        # сделки в r считали contracts от eq (200K). Для компаунда нужен пересчёт...
        # ПРОЩЕ: ROI_год = r['roi'] масштабированный, но contracts зависели от eq.
        # Правильный компаунд: contracts от растущего eq. Считаем точнее вручную ниже.
        pass
    # Точный компаунд: contracts от eq на каждый сигнал
    eq = 200000.0
    eq_history = []
    for y in years:
        y_sigs = sorted([s for s in sigs_all if datetime.fromtimestamp(s['ts'], tz=timezone.utc).year == y],
                        key=lambda x: x['ts'])
        eq_start = eq
        n = 0; wins = 0
        peak_cash = eq; peak_mtm = eq
        cash_mdd = mtm_mdd = 0.0
        for t in y_sigs:
            ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
            bars = t['bars']; pts = bars[:, 0]
            i0 = bisect.bisect_right(pts, t['ts']) - 1
            if i0 < 0: continue
            fill_p = bars[i0, 4] + ms
            j = bisect.bisect_left(pts, t['ts'] + 24 * 3600)
            if j >= len(bars): continue
            exit_p = bars[j, 4] - ms
            risk = risk_map.get(t['tk'], 0.08)
            base_lots = max(1, int(eq * risk / go))
            parts = [(base_lots, fill_p)]
            i_max = min(j, len(bars))
            for k in range(1, 3):
                level = fill_p * (1 + k * 0.5 / 100)
                found = False
                for bi in range(i0, i_max):
                    if bars[bi, 2] >= level:
                        parts.append((base_lots, bars[bi, 2] + ms))
                        found = True
                        break
                if not found: break
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
        roi_y = (eq / eq_start - 1) * 100
        cagr_y = roi_y  # за 1 год CAGR = ROI
        print(f"{y:<6}{n:>6}{roi_y:>+10.1f}{'':>12}{'':>10}{cash_mdd:>9.1f}{mtm_mdd:>8.1f}{wins/n*100:>7.1f}")
        eq_history.append(eq)
    total_roi = (eq / 200000 - 1) * 100
    cagr = ((eq / 200000) ** (1/len(years)) - 1) * 100
    print(f"{'ИТОГО':<6}{'':>6}{'':>10}{total_roi:>+12.1f}{cagr:>+10.1f}")
    return total_roi, cagr

for rm, label in [
    ({'BR': 0.08, 'NG': 0.08, 'SV': 0.05, 'RI': 0.05, 'TT': 0.05}, "MTM~14% (risk 8/5%)"),
    ({'BR': 0.12, 'NG': 0.12, 'SV': 0.08, 'RI': 0.08, 'TT': 0.08}, "MTM~21% (risk 12/8%)"),
    ({'BR': 0.15, 'NG': 0.15, 'SV': 0.10, 'RI': 0.10, 'TT': 0.10}, "MTM~26% (risk 15/10%)"),
]:
    run_by_year(rm, label)
