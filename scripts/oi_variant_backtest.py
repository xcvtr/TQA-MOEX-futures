#!/usr/bin/env python3 -u
"""Бэктест OI-вариантов: LONG-only + hold 120 vs baseline.

Гипотеза из феномен-панели:
- LONG (физ продают → long) работает в 5-7 раз лучше SHORT
- hold 120 мин в 2 раза лучше hold 60

Варианты:
  0 = baseline (long+short, hold 60, pyr3) — как live
  1 = LONG only, hold 60
  2 = LONG only, hold 120
  3 = long+short, hold 120
  4 = LONG only, hold 120, pyr5 (пирамида в откат)
  5 = LONG only, hold 120, без BR (если BR шорт тащил)

OOS: 2021-2023 vs 2024-2026 отдельно.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, RISK, THR, MAX_MARGIN

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def run_portfolio(years, mode, long_only=False, hold=60, pyr=3, tickers=None):
    tickers = tickers or ['NG', 'BR', 'SV']
    data = {}
    for t in tickers:
        data[t] = {}
        for y in years:
            try:
                data[t][y] = load(t, y)
            except Exception:
                pass
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    all_ts = set()
    for t in tickers:
        for y in years:
            if y in data[t]:
                all_ts.update(data[t][y][0].keys())
    all_ts = sorted(all_ts)
    pos = {t: [] for t in tickers}
    occ = {t: None for t in tickers}

    for ts in all_ts:
        for t in tickers:
            for pi in range(len(pos[t]) - 1, -1, -1):
                p = pos[t][pi]
                if ts >= p[3] + 60 * hold:
                    futoi, prices, spec = data[t][p[4]]
                    pts, pprc = prices
                    j = bisect.bisect_right(pts, ts) - 1
                    if j < 0: continue
                    exit_p = pprc[j]
                    ms, sp = spec[1], spec[2]
                    pnl = (exit_p - p[1]) / ms * sp * p[2]
                    if p[0] < 0: pnl = (p[1] - exit_p) / ms * sp * p[2]
                    pnl -= spec[3] * 2 * p[2]
                    eq += pnl; n += 1
                    if pnl > 0: wins += 1
                    pos[t].pop(pi); occ[t] = ts + 300

        for t in tickers:
            futoi = prices = spec = None
            for y in years:
                if y in data[t] and ts in data[t][y][0]:
                    futoi, prices, spec = data[t][y]; break
            if futoi is None: continue
            dn = futoi[ts]
            if len(pos[t]) >= pyr: continue
            if occ[t] and ts < occ[t]: continue
            sig = False; direction = 0
            if dn <= -THR: sig, direction = True, 1
            elif dn >= THR: sig, direction = True, -1
            if not sig: continue
            if long_only and direction != 1: continue  # только LONG
            pts, pprc = prices
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            pt, prc = pts[idx], pprc[idx]
            if prc <= 0 or (ts - pt) > 600: continue
            go, ms, sp, fee = spec
            shares = int(eq * RISK.get(t, 0.25) / go)
            if shares < 1: continue
            used = 0.0
            for tt in tickers:
                for p in pos[tt]:
                    for y in years:
                        if p[4] == y and y in data[tt]:
                            used += p[2] * data[tt][y][2][0]; break
            if (used + shares * go) > eq * MAX_MARGIN:
                shares = max(0, int((eq * MAX_MARGIN - used) / go))
                if shares < 1: continue
            y_ = ts_to_year(ts, years)
            pos[t].append((direction, prc, shares, ts, y_))
            occ[t] = ts + 60 * (hold + 5)

        mv = 0.0
        for t in tickers:
            for p in pos[t]:
                for y in years:
                    if p[4] == y and y in data[t]:
                        futoi, prices, spec = data[t][y]
                        j = bisect.bisect_right(prices[0], ts) - 1
                        if j < 0: break
                        prc = prices[1][j]
                        m = (prc - p[1]) / spec[1] * spec[2] * p[2]
                        if p[0] < 0: m = -m
                        mv += m; break
        peak = max(peak, eq + mv)
        mdd = max(mdd, (peak - (eq + mv)) / peak)

    # eod close
    for t in tickers:
        for p in pos[t]:
            for y in years:
                if p[4] == y and y in data[t]:
                    futoi, prices, spec = data[t][y]
                    exit_p = prices[1][-1]
                    pnl = (exit_p - p[1]) / spec[1] * spec[2] * p[2]
                    if p[0] < 0: pnl = (p[1] - exit_p) / spec[1] * spec[2] * p[2]
                    pnl -= spec[3] * 2 * p[2]
                    eq += pnl; n += 1
                    if pnl > 0: wins += 1
                    break

    roi = (eq - 200000) / 200000 * 100
    wr = wins / n * 100 if n else 0
    return {'roi': round(roi, 1), 'mdd': round(mdd * 100, 1), 'n': n, 'wr': round(wr, 1)}

def ts_to_year(ts, years):
    import datetime as dt
    y = dt.datetime.fromtimestamp(ts - 5 * 3600).year
    return y if y in years else years[0]

VARIANTS = [
    (0, dict(long_only=False, hold=60, pyr=3), 'BASELINE (live)'),
    (1, dict(long_only=True, hold=60, pyr=3), 'LONG only, hold 60'),
    (2, dict(long_only=True, hold=120, pyr=3), 'LONG only, hold 120'),
    (3, dict(long_only=False, hold=120, pyr=3), 'L+S, hold 120'),
    (4, dict(long_only=True, hold=120, pyr=5), 'LONG, hold 120, pyr5'),
]

print(f"{'вариант':<26}{'период':<12}{'ROI%':>10}{'MDD%':>8}{'сдел':>7}{'WR%':>7}")
print("-" * 72)
for vid, kw, name in VARIANTS:
    for label, yrs in [('OOS 21-23', [2021, 2022, 2023]), ('2024-26', [2024, 2025, 2026])]:
        res = run_portfolio(yrs, vid, **kw)
        print(f"{name:<26}{label:<12}{res['roi']:>+10.1f}{res['mdd']:>8.1f}{res['n']:>7}{res['wr']:>7.1f}")
    print()

ch.close()
