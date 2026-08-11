#!/usr/bin/env python3 -u
"""ROI по годам: thr4 exit2 risk 30/20 pyr3 (2023-2026)."""
import sys, bisect
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import psycopg2, scripts.oi_v9_oi_exit as v9

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute('SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs')
v9.specs = {}
for t, go, ms, sp, fee in cur.fetchall(): v9.specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

years = [2023, 2024, 2025, 2026]
data = v9.load_all(years, 4)  # thr=4

def run(risk_ng, risk_small, pyr, pyra_pct, thr=4, exit_thr=2, max_hold=120):
    risk_map = {'BR': risk_ng, 'NG': risk_ng, 'SV': risk_small, 'RI': risk_small, 'TT': risk_small}
    eq = 200000.0; peak_cash = eq; peak_mtm = eq
    cash_mdd = mtm_mdd = 0.0; n = 0; wins = 0
    eq_by_year = {}
    trades_all = []
    for (fut_tk, y), (net_map, bars, spec) in data.items():
        go, ms, sp, fee = spec
        pts = bars[:, 0]
        fts = sorted(net_map.keys())
        for direction in ['long', 'short']:
            pos = None
            for ts in fts:
                dn = net_map[ts]
                if pos is not None:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx < 0: continue
                    cur_p = bars[idx, 4]
                    exit_cond = (dn >= exit_thr) if direction == 'long' else (dn <= -exit_thr)
                    hold_h = (ts - pos['entry_ts']) / 3600
                    if exit_cond or hold_h >= max_hold:
                        exit_p = cur_p - ms if direction == 'long' else cur_p + ms
                        pnl = 0.0
                        for lots, p_in in pos['parts']:
                            if direction == 'long':
                                pnl += ((exit_p - p_in) / ms * sp - fee*2) * lots
                            else:
                                pnl += ((p_in - exit_p) / ms * sp - fee*2) * lots
                        trades_all.append({'ts': ts, 'y': y, 'tk': fut_tk, 'pnl': pnl, 'dir': direction,
                                           'ms': ms, 'sp': sp, 'fee': fee, 'go': go,
                                           'entry_ts': pos['entry_ts'], 'parts': pos['parts'], 'bars': bars})
                        pos = None
                if pos is None:
                    in_cond = (dn <= -thr) if direction == 'long' else (dn >= thr)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx, 4] + ms if direction == 'long' else bars[idx, 4] - ms
                        base_lots = max(1, int(eq * risk_map.get(fut_tk, 0.07) / go))
                        pos = {'entry_ts': ts, 'fill_p': fill_p, 'parts': [(base_lots, fill_p)],
                               'added': 0, 'ms': ms, 'sp': sp, 'fee': fee, 'go': go,
                               'direction': direction, 'bars': bars, 'pts': pts}
                elif pos['added'] < pyr - 1:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        lots_add = max(1, int(eq * risk_map.get(fut_tk, 0.07) / pos['go']))
                        if direction == 'long':
                            hi = bars[idx, 2]
                            gain_pct = (hi - pos['fill_p']) / pos['fill_p'] * 100
                            if gain_pct >= (pos['added'] + 1) * pyra_pct:
                                pos['parts'].append((lots_add, hi + ms))
                                pos['added'] += 1
                        else:
                            lo = bars[idx, 3]
                            gain_pct = (pos['fill_p'] - lo) / pos['fill_p'] * 100
                            if gain_pct >= (pos['added'] + 1) * pyra_pct:
                                pos['parts'].append((lots_add, lo - ms))
                                pos['added'] += 1
    for t in sorted(trades_all, key=lambda x: x['ts']):
        eq += t['pnl']; n += 1
        if t['pnl'] > 0: wins += 1
        peak_cash = max(peak_cash, eq)
        cash_mdd = max(cash_mdd, (peak_cash - eq) / peak_cash * 100)
        eq_by_year[t['y']] = eq
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['entry_ts']) - 1
        i1 = bisect.bisect_right(pts, t['ts']) - 1
        for bi in range(max(0,i0), min(i1+1, len(bars))):
            lo = bars[bi, 3]
            mtm_pnl = 0.0
            for lots, p_in in t['parts']:
                if t['dir'] == 'long':
                    mtm_pnl += ((lo - p_in) / t['ms'] * t['sp'] - t['fee']*2) * lots
                else:
                    mtm_pnl += ((p_in - lo) / t['ms'] * t['sp'] - t['fee']*2) * lots
            mtm_eq = (eq - t['pnl']) + mtm_pnl
            peak_mtm = max(peak_mtm, mtm_eq)
            mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100 if peak_mtm > 0 else 0)
    cagr = ((eq/200000)**(1/len(years)) - 1)*100
    return {'cagr': round(cagr,1), 'cash_mdd': round(cash_mdd,1), 'mtm_mdd': round(mtm_mdd,1),
            'wr': round(wins/n*100,1), 'n': n, 'eq_by_year': eq_by_year}

for rng, rsm, pyr, pp, label in [
    (0.30, 0.20, 3, 0.5, "thr4 exit2 risk 30/20 pyr3"),
    (0.40, 0.30, 3, 0.5, "thr4 exit2 risk 40/30 pyr3"),
]:
    r = run(rng, rsm, pyr, pp)
    print(f"\n=== {label} ===")
    print(f"{'год':<6}{'eq':>12}{'ROI_год':>10}")
    prev = 200000.0
    for y in years:
        eq_y = r['eq_by_year'].get(y, prev)
        print(f"{y:<6}{eq_y:>12,.0f}{(eq_y/prev-1)*100:>+10.1f}")
        prev = eq_y
    print(f"CAGR {r['cagr']}%, CashMDD {r['cash_mdd']}%, MTM {r['mtm_mdd']}%, WR {r['wr']}%, {r['n']} сделок")
