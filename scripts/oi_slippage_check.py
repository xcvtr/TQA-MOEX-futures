#!/usr/bin/env python3 -u
"""Влияние slippage (2-5 тиков, как в live) на LONG+h120×1.5.

Бэктест входит без slippage (цена на ts), live — +2-5 тиков в худшую сторону.
Сравниваем ROI/MDD с live-slippage и без.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from scripts.oi_vol_filter_backtest import load, THR, MAX_MARGIN

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def run(years, slip_ticks=0, hold=120, pyr=3, risk_mult=1.5):
    tickers = ['NG', 'BR', 'SV']
    RISK = {'NG': 0.25 * risk_mult, 'BR': 0.25 * risk_mult, 'SV': 0.15 * risk_mult}
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
        for t in tickers:
            for pi in range(len(pos[t]) - 1, -1, -1):
                p = pos[t][pi]
                if ts >= p[3] + 60 * hold:
                    futoi, prices, spec = data[t][p[4]]
                    j = bisect.bisect_right(prices[0], ts) - 1
                    if j < 0: continue
                    exit_p = prices[1][j]
                    ms, sp = spec[1], spec[2]
                    if slip_ticks:  # выход тоже со slippage (в худшую сторону)
                        exit_p = exit_p - ms * slip_ticks if p[0] > 0 else exit_p + ms * slip_ticks
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
            if dn > -THR: continue
            if len(pos[t]) >= pyr: continue
            if occ[t] and ts < occ[t]: continue
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
            if slip_ticks:  # вход со slippage
                prc = prc + ms * slip_ticks
            import datetime as dt
            y_ = dt.datetime.fromtimestamp(ts - TZ_SHIFT).year
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
                    if slip_ticks:
                        exit_p = exit_p - ms * slip_ticks if p[0] > 0 else exit_p + ms * slip_ticks
                    pnl = (exit_p - p[1]) / spec[1] * spec[2] * p[2]
                    if p[0] < 0: pnl = (p[1] - exit_p) / spec[1] * spec[2] * p[2]
                    pnl -= spec[3] * 2 * p[2]
                    eq += pnl; n += 1
                    if pnl > 0: wins += 1
                    break

    roi = (eq - 200000) / 200000 * 100
    return {'roi': round(roi,1), 'mdd': round(mdd*100,1), 'n': n,
            'wr': round(wins/n*100,1) if n else 0}

TZ_SHIFT = 5 * 3600

print(f"{'slippage':<14}{'период':<12}{'ROI%':>10}{'MDD%':>8}{'сдел':>7}{'WR%':>7}")
print("-" * 58)
for sl, slname in [(0, '0 тиков (тестер)'), (2, '2 тика (live min)'), (3, '3 тика (live mid)'), (5, '5 тиков (live max)')]:
    for pname, yrs in [('2024-26', [2024,2025,2026])]:
        r = run(yrs, slip_ticks=sl)
        print(f"{slname:<14}{pname:<12}{r['roi']:>+10.1f}{r['mdd']:>8.1f}{r['n']:>7}{r['wr']:>7.1f}")
    print()
ch.close()
