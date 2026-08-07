#!/usr/bin/env python3
"""Портфельный бэктестер OI (NG + SV, pyr3) — общий equity, конкуренция за маржу."""
import sys, json, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

TZ_SHIFT = 5 * 3600
MT = {'SV': 'SILV', 'NG': 'NG', 'BR': 'BR'}


def load(ticker, year):
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    START, END = f'{year}-01-01', f'{year}-12-31'
    if year == 2026: END = '2026-08-07'
    def q(sql): return ch.query(sql).result_rows
    ft_map = {'SV': 'SV', 'NG': 'NG', 'BR': 'BR'}
    r = q(f"SELECT bt, (buy_fiz - sell_fiz) * 1.0 / NULLIF(buy_fiz + sell_fiz, 0) * 100 as dn "
          f"FROM moex.futoi WHERE ticker='{ft_map[ticker]}' AND bt >= '{START} 00:00:00' AND bt <= '{END} 23:59:59'")
    futoi = {bt.replace(tzinfo=None).timestamp() + TZ_SHIFT: dn for bt, dn in r}
    r = q(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
          f"WHERE ticker='{MT[ticker]}' AND bt >= '{START}' AND bt <= '{END} 23:59:59'")
    rows = [(ts, c) for ts, c in r if c and c > 0]
    arr = np.array(rows, dtype=np.float64)
    order = np.argsort(arr[:, 0])
    prices = (arr[order, 0], arr[order, 1])
    pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
    cur = pg.cursor()
    cur.execute("SELECT go, min_step, step_price, fee_entry FROM futures.ticker_specs WHERE ticker=%s", (ticker,))
    row = cur.fetchone()
    pg.close()
    ch.close()
    return futoi, prices, (float(row[0]), float(row[1]), float(row[2]), float(row[3]))


def run_portfolio(year, risk_map, thr_map, hold=60, max_pos=3, max_margin=0.9):
    """risk_map: {ticker: risk}, thr_map: {ticker: thr}"""
    tickers = list(risk_map.keys())
    data = {t: load(t, year) for t in tickers}
    specs = {t: data[t][2] for t in tickers}
    fts_map = {t: sorted(data[t][0].keys()) for t in tickers}
    pts_map = {t: data[t][1][0] for t in tickers}

    all_t = sorted(set().union(*[set(f) for f in fts_map.values()]))
    eq = 200000.0
    peak = eq
    mdd = 0.0
    n = 0
    wins = 0
    pnl_sum = 0.0
    by_t = {t: 0.0 for t in tickers}
    pos = {t: [] for t in tickers}  # list of (entry_ts, dir, shares, entry_p)
    occ = {t: None for t in tickers}

    for ts in all_t:
        # timeout close
        for t in tickers:
            for pi in range(len(pos[t]) - 1, -1, -1):
                p = pos[t][pi]
                if ts >= p[0] + 60 * hold:
                    idx = bisect.bisect_right(pts_map[t], ts) - 1
                    if idx < 0: continue
                    prc = data[t][1][1][idx]
                    pnl = (prc - p[3]) / specs[t][1] * specs[t][2] * p[2]
                    if p[1] < 0: pnl = (p[3] - prc) / specs[t][1] * specs[t][2] * p[2]
                    pnl -= specs[t][3]
                    eq += pnl; n += 1
                    if pnl > 0: wins += 1
                    pnl_sum += pnl
                    by_t[t] += pnl
                    pos[t].pop(pi)
                    occ[t] = ts + 300

        # entry
        for t in tickers:
            dn = data[t][0].get(ts)
            if dn is None: continue
            if len(pos[t]) >= max_pos: continue
            if occ[t] and ts < occ[t]: continue
            thr = thr_map[t]
            sig = False
            direction = 0
            if dn <= -thr: sig, direction = True, 1
            elif dn >= thr: sig, direction = True, -1
            if not sig: continue
            idx = bisect.bisect_right(pts_map[t], ts) - 1
            if idx < 0: continue
            pt, prc = data[t][1][0][idx], data[t][1][1][idx]
            if prc <= 0 or (ts - pt) > 600: continue
            go, ms, sp, fee = specs[t]
            shares = int(eq * risk_map[t] / go)
            if shares < 1: continue
            # маржа: общий лимит
            used = sum(p[2] * specs[tt][0] for tt in tickers for p in pos[tt])
            if (used + shares * go) > eq * max_margin:
                shares = max(0, int((eq * max_margin - used) / go))
                if shares < 1: continue
            pos[t].append((ts, direction, shares, prc))
            occ[t] = ts + 60 * (hold + 5)

        # MTM
        mv = 0.0
        for t in tickers:
            for p in pos[t]:
                idx = bisect.bisect_right(pts_map[t], ts) - 1
                if idx < 0: continue
                prc = data[t][1][1][idx]
                m = (prc - p[3]) / specs[t][1] * specs[t][2] * p[2]
                if p[1] < 0: m = -m
                mv += m
        peak = max(peak, eq + mv)
        mdd = max(mdd, (peak - (eq + mv)) / peak)

    # eod close
    for t in tickers:
        for p in pos[t]:
            prc = data[t][1][1][-1]
            pnl = (prc - p[3]) / specs[t][1] * specs[t][2] * p[2]
            if p[1] < 0: pnl = (p[3] - prc) / specs[t][1] * specs[t][2] * p[2]
            pnl -= specs[t][3]
            eq += pnl; n += 1
            if pnl > 0: wins += 1
            pnl_sum += pnl
            by_t[t] += pnl

    roi = (eq - 200000) / 200000 * 100
    wr = wins / n * 100 if n else 0
    return {'year': year, 'roi': round(roi, 1), 'mdd': round(mdd * 100, 1), 'n': n,
            'wr': round(wr, 1), 'by_ticker': {k: round(v) for k, v in by_t.items()}}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2026)
    ap.add_argument('--risk-ng', type=float, default=0.15)
    ap.add_argument('--risk-sv', type=float, default=0.05)
    ap.add_argument('--risk-br', type=float, default=0.20)
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    res = run_portfolio(args.year, {'NG': args.risk_ng, 'SV': args.risk_sv, 'BR': args.risk_br},
                        {'NG': 3.0, 'SV': 3.0, 'BR': 3.0})
    print(json.dumps(res))
    if args.out:
        with open(args.out, 'w') as f: json.dump(res, f)
