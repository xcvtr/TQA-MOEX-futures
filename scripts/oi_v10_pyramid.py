#!/usr/bin/env python3 -u
"""OI v10 — LONG+SHORT с ОИ-выходом + пирамидинг + высокий риск.

Вход:  |day_net| >= 5 (long при <= -5, short при >= +5)
Выход: day_net достиг ±3 (обратное условие) или 120ч
Пирамидинг: +1 лот при +pyra_pct% от входа (по hi/lo бара), до pyr частей
Риск: 30-50% на NG/BR, 20-35% на SV/RI/TT
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

def run(risk_ng, risk_small, pyr, pyra_pct, label):
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
                                           'ms': ms, 'sp': sp, 'fee': fee, 'go': go,
                                           'entry_ts': pos['entry_ts'], 'parts': pos['parts'], 'bars': bars})
                        pos = None
                if pos is None:
                    in_cond = (dn <= -5) if direction == 'long' else (dn >= 5)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx, 4] + ms if direction == 'long' else bars[idx, 4] - ms
                        base_lots = max(1, int(eq * risk_map.get(fut_tk, 0.07) / go))
                        pos = {'entry_ts': ts, 'fill_p': fill_p, 'parts': [(base_lots, fill_p)],
                               'added': 0, 'ms': ms, 'sp': sp, 'fee': fee, 'go': go,
                               'direction': direction, 'bars': bars, 'pts': pts}
                elif pos['added'] < pyr - 1:
                    # пирамидинг: проверяем достижение уровня +pyra_pct% от входа
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        if direction == 'long':
                            hi = bars[idx, 2]
                            gain_pct = (hi - pos['fill_p']) / pos['fill_p'] * 100
                            if gain_pct >= (pos['added'] + 1) * pyra_pct:
                                pos['parts'].append((base_lots if False else max(1, int(eq * risk_map.get(fut_tk, 0.07) / pos['go'])),
                                                     hi + ms))
                                pos['added'] += 1
                        else:
                            lo = bars[idx, 3]
                            gain_pct = (pos['fill_p'] - lo) / pos['fill_p'] * 100
                            if gain_pct >= (pos['added'] + 1) * pyra_pct:
                                pos['parts'].append((max(1, int(eq * risk_map.get(fut_tk, 0.07) / pos['go'])),
                                                     lo - ms))
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
    rois = []
    prev = 200000.0
    for y in years:
        eq_y = eq_by_year.get(y, prev)
        rois.append((eq_y/prev - 1)*100)
        prev = eq_y
    return {'cagr': round(cagr,1), 'cash_mdd': round(cash_mdd,1), 'mtm_mdd': round(mtm_mdd,1),
            'wr': round(wins/n*100,1), 'n': n, 'rois': rois}

print(f"{'конфиг':<22}{'CAGR%':>8}{'CashMDD':>9}{'MTM MDD':>9}{'WR%':>7}{'мин_год':>9}{'макс_год':>10}")
print("-" * 74)
for rng, rsm, pyr, pp in [
    (0.30, 0.20, 1, 0.5),
    (0.30, 0.20, 2, 0.5),
    (0.30, 0.20, 3, 0.5),
    (0.40, 0.30, 2, 0.5),
    (0.40, 0.30, 3, 0.5),
    (0.50, 0.35, 3, 0.5),
    (0.50, 0.35, 3, 1.0),
    (0.50, 0.35, 5, 0.5),
]:
    r = run(rng, rsm, pyr, pp, f"risk {rng:.0%} pyr{pyr} pp{pp}")
    print(f"{f'risk {rng:.0%}/{rsm:.0%} pyr{pyr} pp{pp}':<22}{r['cagr']:>8.1f}{r['cash_mdd']:>9.1f}"
          f"{r['mtm_mdd']:>9.1f}{r['wr']:>7.1f}{min(r['rois']):>+9.1f}{max(r['rois']):>+10.1f}")
