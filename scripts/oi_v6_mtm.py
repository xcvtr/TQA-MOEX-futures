#!/usr/bin/env python3 -u
"""OI v6 — MTM DD (mark-to-market) + Cash DD, скан риска до MTM DD ≤ 15%.

Отличие от v5: трекинг equity по каждому бару внутри позиции (не только на закрытии).
MTM DD = просадка с учётом открытых позиций. Cash DD = по закрытым сделкам.

Цель: подобрать риск так, чтобы MTM DD ≈ 15% (максимум доходности при DD ≤ 15%).
"""
import sys, bisect, argparse
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
DAY_SEC = 86400

ALL = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV', 'RI': 'RTSI', 'TT': 'TATN'}

def irk_day(ts):
    return int((ts - 7 * 3600) // DAY_SEC)

def load_tk(fut_tk, mt_tk, y):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur "
                 f"FROM moex.futoi WHERE ticker='{fut_tk}' "
                 f"AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    day_start = {}
    net_map = {}
    for ts, fb, fs, yb, ys in r:
        d = irk_day(ts)
        if d not in day_start:
            day_start[d] = int(fb) - int(fs)
        total = int(fb) + int(fs) + int(yb) + int(ys)
        if total <= 0: continue
        net_map[ts] = (int(fb) - int(fs) - day_start[d]) / total * 100
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc FROM moex.mt5_continuous "
                  f"WHERE ticker='{mt_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, o, h, l, c) for ts, o, h, l, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    return net_map, arr[o]

def gen_signals(years, thr):
    sigs = []
    for fut_tk, mt_tk in ALL.items():
        spec = specs.get(fut_tk)
        if spec is None: continue
        for y in years:
            d = load_tk(fut_tk, mt_tk, y)
            if d is None: continue
            net_map, bars = d
            pts = bars[:, 0]
            go, ms, sp, fee = spec
            day_best = {}
            for ts in sorted(net_map.keys()):
                dn = net_map[ts]
                if dn > -thr: continue
                idx = bisect.bisect_right(pts, ts) - 1
                if idx < 0: continue
                prc = bars[idx, 4]
                if prc <= 0 or (ts - pts[idx]) > 600: continue
                dnum = irk_day(ts)
                if dnum not in day_best or abs(dn) > abs(day_best[dnum]['dn']):
                    day_best[dnum] = {'ts': ts, 'prc': prc, 'ms': ms, 'sp': sp,
                                      'fee': fee, 'go': go, 'dn': dn}
            for dnum, t in day_best.items():
                t['tk'] = fut_tk; t['dnum'] = dnum; t['bars'] = bars
                sigs.append(t)
    return sigs

def backtest_mtm(sigs, years, risk_map, slip=1, pyr=3, pyra_pct=0.5):
    """MTM-трекинг: equity по каждому бару. Возвращает cash и mtm DD."""
    eq = 200000.0
    peak_cash = eq
    peak_mtm = eq
    cash_mdd = 0.0
    mtm_mdd = 0.0
    n = 0; wins = 0
    for t in sorted(sigs, key=lambda x: x['ts']):
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['ts']) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip
        j = bisect.bisect_left(pts, t['ts'] + 24 * 3600)
        if j >= len(bars): continue
        exit_p = bars[j, 4] - ms * slip
        risk = risk_map.get(t['tk'], 0.15)
        base_lots = max(1, int(eq * risk / go))
        parts = [(base_lots, fill_p)]
        if pyr > 1:
            max_i = min(j, len(bars))
            for k in range(1, pyr):
                level = fill_p * (1 + k * pyra_pct / 100)
                found = False
                for bi in range(i0, max_i):
                    if bars[bi, 2] >= level:
                        parts.append((base_lots, bars[bi, 2] + ms * slip))
                        found = True
                        break
                if not found:
                    break
        pnl = 0.0
        for lots, p_in in parts:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak_cash = max(peak_cash, eq)
        cash_mdd = max(cash_mdd, (peak_cash - eq) / peak_cash * 100)
        # MTM: проход по барам позиции — худшая цена между входом и выходом
        # для каждой части: mtm pnl = (lo - p_in)/ms*sp*lots (лонг, худший случай)
        for bi in range(i0, min(j, len(bars))):
            lo = bars[bi, 3]
            mtm_pnl = 0.0
            for lots, p_in in parts:
                mtm_pnl += ((lo - p_in) / ms * sp - fee * 2) * lots
            mtm_eq = eq - pnl + mtm_pnl  # equity до сделки + текущий mtm
            peak_mtm = max(peak_mtm, mtm_eq)
            mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100 if peak_mtm > 0 else 0)
    per_year = n / len(years)
    cagr = ((1 + (eq-200000)/200000) ** (1/len(years)) - 1) * 100 if eq > 0 else -100
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'cash_mdd': round(cash_mdd,1), 'mtm_mdd': round(mtm_mdd,1),
            'wr': round(wins/n*100,1) if n else 0, 'cagr': round(cagr,1)}

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--thr', type=float, default=8.0)
    ap.add_argument('--risk', type=float, default=0.20)
    ap.add_argument('--risk-small', type=float, default=0.15)
    ap.add_argument('--slip', type=int, default=1)
    ap.add_argument('--pyr', type=int, default=3)
    ap.add_argument('--pyra-pct', type=float, default=0.5)
    args = ap.parse_args()

    pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
    cur = pg.cursor()
    cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
    global specs
    specs = {}
    for t, go, ms, sp, fee in cur.fetchall():
        specs[t] = (float(go), float(ms), float(sp), float(fee))
    pg.close()

    years = [2022, 2023, 2024, 2025, 2026]
    sigs = gen_signals(years, args.thr)
    risk_map = {'BR': args.risk, 'NG': args.risk, 'SV': args.risk_small,
                'RI': args.risk_small, 'TT': args.risk_small}
    r = backtest_mtm(sigs, years, risk_map, slip=args.slip, pyr=args.pyr, pyra_pct=args.pyra_pct)
    print(f"Сигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год (thr={args.thr})")
    print(f"risk={risk_map} pyr={args.pyr} pyra={args.pyra_pct}%")
    print(f"ИТОГ: ROI {r['roi']:+.1f}% за 5 лет, CAGR {r['cagr']:.1f}%, "
          f"Cash MDD {r['cash_mdd']:.1f}%, MTM MDD {r['mtm_mdd']:.1f}%, "
          f"WR {r['wr']:.1f}%, {r['n']} сделок ({r['per_year']:.0f}/год)")
    ch.close()
