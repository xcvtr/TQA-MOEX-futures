#!/usr/bin/env python3 -u
"""LONG-only hold 120 — динамические режимы.

База: LONG+h120, risk 0.25/0.25/0.15, pyr3.

Динамика:
  DD-control: риск ×0.5 при DD от пика >10%, ×0.3 при >20% (возврат к 1.0 после нового пика)
  LQ (Local Quality): размер по |dn| — из панели: 4-8 лучшие (×1.5), 8-12 (×1.0), >12 (×0.6)

Варианты:
  0 = фикс ×1.5 (последний)
  1 = DD-control
  2 = LQ по |dn|
  3 = DD-control + LQ
  4 = DD-control + LQ + pyr4
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR, MAX_MARGIN

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
BASE_RISK = {'NG': 0.25, 'BR': 0.25, 'SV': 0.15}
BASE_MULT = 1.5  # последний вариант: риск ×1.5

def lq_mult(dn):
    a = abs(dn)
    if a < 8: return 1.5
    if a < 12: return 1.0
    return 0.6

def dd_mult(dd_pct):
    if dd_pct > 20: return 0.3
    if dd_pct > 10: return 0.5
    return 1.0

def run(years, mode, hold=120, pyr=3):
    tickers = ['NG', 'BR', 'SV']
    data = {}
    for t in tickers:
        data[t] = {}
        for y in years:
            try: data[t][y] = load(t, y)
            except Exception: pass
    eq = 200000.0; peak = eq; mdd = 0.0; n = 0; wins = 0
    all_ts = set()
    for t in tickers:
        for y in years:
            if y in data[t]: all_ts.update(data[t][y][0].keys())
    all_ts = sorted(all_ts)
    pos = {t: [] for t in tickers}; occ = {t: None for t in tickers}

    for ts in all_ts:
        # текущий DD для динамики
        cur_dd = (peak - eq) / peak * 100 if peak > 0 else 0
        ddm = dd_mult(cur_dd) if mode in (1, 3, 4) else 1.0

        for t in tickers:
            for pi in range(len(pos[t]) - 1, -1, -1):
                p = pos[t][pi]
                if ts >= p[3] + 60 * hold:
                    futoi, prices, spec = data[t][p[4]]
                    j = bisect.bisect_right(prices[0], ts) - 1
                    if j < 0: continue
                    exit_p = prices[1][j]; ms, sp = spec[1], spec[2]
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
            if dn > -THR: continue  # только LONG
            if len(pos[t]) >= pyr: continue
            if occ[t] and ts < occ[t]: continue
            pts, pprc = prices
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            pt, prc = pts[idx], pprc[idx]
            if prc <= 0 or (ts - pt) > 600: continue
            go, ms, sp, fee = spec
            # динамический риск
            risk = BASE_RISK.get(t, 0.25) * BASE_MULT
            if mode in (2, 3, 4):
                risk *= lq_mult(dn)
            risk *= ddm
            shares = int(eq * risk / go)
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
            import datetime as dt
            y_ = dt.datetime.fromtimestamp(ts - 5*3600).year
            y_ = y_ if y_ in years else years[0]
            pos[t].append((1, prc, shares, ts, y_))
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
                        mv += m; break
        peak = max(peak, eq + mv)
        mdd = max(mdd, (peak - (eq + mv)) / peak)

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
    return {'roi': round(roi,1), 'mdd': round(mdd*100,1), 'n': n,
            'wr': round(wins/n*100,1) if n else 0}

MODES = {
    0: 'фикс ×1.5 (база)',
    1: 'DD-control',
    2: 'LQ по |dn|',
    3: 'DD-control + LQ',
    4: 'DD-control + LQ + pyr4',
}
print(f"{'режим':<26}{'период':<12}{'ROI%':>10}{'MDD%':>8}{'сдел':>7}{'WR%':>7}")
print("-" * 72)
for mid, name in MODES.items():
    kw = {'pyr': 4} if mid == 4 else {'pyr': 3}
    for label, yrs in [('OOS 21-23', [2021,2022,2023]), ('2024-26', [2024,2025,2026])]:
        r = run(yrs, mid, **kw)
        print(f"{name:<26}{label:<12}{r['roi']:>+10.1f}{r['mdd']:>8.1f}{r['n']:>7}{r['wr']:>7.1f}")
    print()
ch.close()
