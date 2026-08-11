#!/usr/bin/env python3 -u
"""Полный бэктест с per-ticker thr и расширенным портфелем.

Конфиг: NG/BR thr=4, SV/TT/RI/Rn thr=5, pyr3, pyra 0.5%, hold 24ч.
Сравнение с базой (все thr=5, 5 тикеров).
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

# Расширенный ALL (добавляем RN)
import scripts.oi_v7_mgmt as v7mod
v7mod.ALL = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV', 'RI': 'RTSI', 'TT': 'TATN', 'RN': 'RN'}

def run_cfg(thr_map, risk_map, label):
    sigs = v7.gen_signals_per_thr(years, thr_map) if hasattr(v7, 'gen_signals_per_thr') else None
    # свой сбор с per-ticker thr
    all_sigs = []
    for fut_tk, mt_tk in v7mod.ALL.items():
        thr = thr_map.get(fut_tk, 5)
        for y in years:
            d = v7.load_tk(fut_tk, mt_tk, y)
            if d is None: continue
            net_map, bars = d
            pts = bars[:, 0]
            go, ms, sp, fee = v7.specs[fut_tk]
            day_best = {}
            for ts in sorted(net_map.keys()):
                dn = net_map[ts]
                if dn > -thr: continue
                idx = bisect.bisect_right(pts, ts) - 1
                if idx < 0: continue
                prc = bars[idx, 4]
                if prc <= 0 or (ts - pts[idx]) > 600: continue
                dnum = v7.irk_day(ts)
                if dnum not in day_best or abs(dn) > abs(day_best[dnum]['dn']):
                    day_best[dnum] = {'ts': ts, 'prc': prc, 'ms': ms, 'sp': sp,
                                      'fee': fee, 'go': go, 'dn': dn}
            for dnum, t in day_best.items():
                t['tk'] = fut_tk; t['dnum'] = dnum; t['bars'] = bars
                all_sigs.append(t)
    r = v7.backtest(all_sigs, years, risk_map, pyr=3, pyra_pct=0.5, horizon_h=24)
    print(f"{label}: {len(all_sigs)} сигн ({len(all_sigs)/5:.0f}/год), ROI {r['roi']:+.1f}%, "
          f"CAGR {r['cagr']:.1f}%, CashMDD {r['cash_mdd']:.1f}%, MTM {r['mtm_mdd']:.1f}%, "
          f"WR {r['wr']:.1f}%")
    # по годам
    prev = 200000.0
    for y in years:
        eq_y = r['eq_by_year'].get(y, prev)
        print(f"  {y}: ROI_год={(eq_y/prev-1)*100:+.1f}%")
        prev = eq_y
    return r

print("=== БАЗА (thr5, 5 тикеров: NG BR SV RI TT) ===")
run_cfg({}, {'BR': 0.08, 'NG': 0.08, 'SV': 0.05, 'RI': 0.05, 'TT': 0.05}, "thr5 все")

print("\n=== РАСШИРЕННЫЙ (thr4 NG/BR, thr5 ост, +RN) ===")
thr_map = {'NG': 4, 'BR': 4, 'SV': 5, 'RI': 5, 'TT': 5, 'RN': 5}
risk_map = {'BR': 0.08, 'NG': 0.08, 'SV': 0.05, 'RI': 0.05, 'TT': 0.05, 'RN': 0.04}
run_cfg(thr_map, risk_map, "thr4 NG/BR + RN")

print("\n=== РАСШИРЕННЫЙ thr3 NG/BR (максимум сделок) ===")
thr_map2 = {'NG': 3, 'BR': 3, 'SV': 5, 'RI': 5, 'TT': 5, 'RN': 5}
run_cfg(thr_map2, risk_map, "thr3 NG/BR + RN")
