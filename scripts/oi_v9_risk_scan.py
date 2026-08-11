#!/usr/bin/env python3 -u
"""Подъём риска для LONG+SHORT с ОИ-выходом (exit_thr=3, hold 120).

Сканируем базу риска 7/4% → 20/15%. Смотрим CAGR, Cash MDD, MTM MDD.
"""
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

years = [2022, 2023, 2024, 2025, 2026]
data = v9.load_all(years, 5)

def run(risk_ng, risk_small, label):
    """LONG+SHORT, ОИ-выход exit_thr=3, hold 120, риск risk_ng/risk_small."""
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
                    exit_cond = (dn >= 3) if direction == 'long' else (dn <= -3)
                    hold_h = (ts - pos['entry_ts']) / 3600
                    if exit_cond or hold_h >= 120:
                        exit_p = cur_p - ms if direction == 'long' else cur_p + ms
                        pnl = 0.0
                        for lots, p_in in pos['parts']:
                            if direction == 'long':
                                pnl += ((exit_p - p_in) / ms * sp - fee*2) * lots
                            else:
                                pnl += ((p_in - exit_p) / ms * sp - fee*2) * lots
                        trades_all.append({'ts': ts, 'y': y, 'tk': fut_tk, 'pnl': pnl, 'dir': direction,
                                           'ms': ms, 'sp': sp, 'fee': fee, 'go': go, 'parts': pos['parts']})
                        pos = None
                if pos is None:
                    in_cond = (dn <= -5) if direction == 'long' else (dn >= 5)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx, 4] + ms if direction == 'long' else bars[idx, 4] - ms
                        base_lots = max(1, int(eq * risk_map.get(fut_tk, 0.07) / go))
                        pos = {'entry_ts': ts, 'parts': [(base_lots, fill_p)]}
    # симуляция (mtm по ходу)
    for t in sorted(trades_all, key=lambda x: x['ts']):
        eq += t['pnl']; n += 1
        if t['pnl'] > 0: wins += 1
        peak_cash = max(peak_cash, eq)
        cash_mdd = max(cash_mdd, (peak_cash - eq) / peak_cash * 100)
        eq_by_year[t['y']] = eq
    cagr = ((eq/200000)**(1/len(years)) - 1)*100
    return {'cagr': round(cagr,1), 'cash_mdd': round(cash_mdd,1), 'n': n,
            'wr': round(wins/n*100,1) if n else 0}

print(f"{'риск NG/BR':<14}{'риск др':<10}{'сдел':>6}{'/год':>5}{'CAGR%':>8}{'CashMDD':>9}{'WR%':>7}")
print("-" * 60)
for rng, rsm in [(0.07, 0.04), (0.10, 0.07), (0.12, 0.08), (0.15, 0.10), (0.18, 0.12), (0.20, 0.15)]:
    r = run(rng, rsm, f"risk {rng}/{rsm}")
    print(f"{rng:<14.0%}{rsm:<10.0%}{r['n']:>6}{r['n']/5:>5.0f}{r['cagr']:>8.1f}{r['cash_mdd']:>9.1f}{r['wr']:>7.1f}")
