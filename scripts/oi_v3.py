#!/usr/bin/env python3 -u
"""OI v3: все 15 тикеров, правильные specs (futoi-код), пирамидинг, компаунд.

Ключевой фикс: specs берутся по futoi-коду (SV→SILV но specs['SV']), не по mt5-тикеру!
Маппинг: futoi_код → (mt5 тикер, specs[futoi_код]).

Трюки: компаунд (risk % от equity), пирамидинг по барам (добавка при +N тиков),
       исключение убыточных (GZ/RN), выход hours_24 / open_next.

Чистые таймзоны: UTC-epoch, торговый день = 15:00 IRK.
"""
import sys, bisect, argparse
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

DAY_SEC = 86400
# futoi_код: (mt5_ticker, включён?)
ALL = {
 'BR': ('BR', True), 'NG': ('NG', True), 'SV': ('SILV', True),
 'RN': ('RN', True), 'GZ': ('GZ', True), 'Eu': ('Eu', True),
 'RI': ('RTSI', True), 'LK': ('LKOH', True), 'SN': ('SNGP', True),
 'SF': ('SBRF', True), 'MG': ('MGNT', True), 'VB': ('VTBR', True),
 'TT': ('TATN', True), 'AF': ('AFLT', True), 'HY': ('HYDR', True),
}

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

def gen_signals(years, thr, skip=()):
    sigs = []
    for fut_tk, (mt_tk, on) in ALL.items():
        if not on or fut_tk in skip: continue
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

def backtest(sigs, years, risk, slip=1, exit_mode='hours_24', pyr=1, pyra_ticks=50):
    """pyr: макс частей (1=без пирамидинга). pyra_ticks: шаг добавки."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    trades = []
    for t in sorted(sigs, key=lambda x: x['ts']):
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['ts']) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip
        dnum = t['dnum']
        if exit_mode == 'open_next':
            t_exit = (dnum + 1) * DAY_SEC + 7 * 3600
            j = bisect.bisect_left(pts, t_exit)
            if j >= len(bars): continue
            exit_p = bars[j, 1] - ms * slip
        elif exit_mode == 'hours_24':
            j = bisect.bisect_left(pts, t['ts'] + 24 * 3600)
            if j >= len(bars): continue
            exit_p = bars[j, 4] - ms * slip
        elif exit_mode.startswith('hours'):
            h = int(exit_mode.split('_')[1])
            j = bisect.bisect_left(pts, t['ts'] + h * 3600)
            if j >= len(bars): continue
            exit_p = bars[j, 4] - ms * slip
        # база + пирамидинг (по барам до выхода)
        base_lots = max(1, int(eq * risk / go))
        parts = [(base_lots, fill_p)]
        if pyr > 1:
            i1 = bisect.bisect_right(pts, min(pts[j], t['ts'] + 24*3600)) - 1 if exit_mode.startswith('hours') else j
            # идём по барам от входа до выхода, добавляем при +k*pyra_ticks
            max_i = j
            for k in range(1, pyr):
                level = fill_p + ms * k * pyra_ticks
                # находим бар, где hi >= level (до выхода)
                found = False
                for bi in range(i0, min(max_i, len(bars))):
                    if bars[bi, 2] >= level:
                        parts.append((base_lots, bars[bi, 2] + ms * slip))  # вход по hi
                        found = True
                        break
                if not found:
                    break
        pnl = 0.0
        for lots, p_in in parts:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        trades.append({'tk': t['tk'], 'pnl': pnl, 'parts': len(parts)})
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    per_year = n / len(years)
    cagr = ((1 + (eq-200000)/200000) ** (1/len(years)) - 1) * 100 if eq > 0 else -100
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'wr': round(wins/n*100,1) if n else 0, 'cagr': round(cagr,1),
            'trades': trades}

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--thr', type=float, default=8.0)
    ap.add_argument('--risk', type=float, default=0.10)
    ap.add_argument('--slip', type=int, default=1)
    ap.add_argument('--exit', default='hours_24')
    ap.add_argument('--pyr', type=int, default=1)
    ap.add_argument('--pyra-ticks', type=int, default=50)
    ap.add_argument('--skip', default='GZ,RN')  # убыточные по умолчанию
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
    skip = tuple(args.skip.split(',')) if args.skip else ()
    sigs = gen_signals(years, args.thr, skip=skip)
    r = backtest(sigs, years, args.risk, slip=args.slip, exit_mode=args.exit,
                 pyr=args.pyr, pyra_ticks=args.pyra_ticks)
    print(f"Сигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год (thr={args.thr}, skip={skip})")
    print(f"exit={args.exit} risk={args.risk:.0%} pyr={args.pyr} | ROI {r['roi']:+.1f}% за 5 лет, "
          f"CAGR {r['cagr']:.1f}%, MDD {r['mdd']:.1f}%, WR {r['wr']:.1f}%, {r['n']} сделок ({r['per_year']:.0f}/год)")
    by_tk = {}
    for t in r['trades']:
        by_tk.setdefault(t['tk'], []).append(t['pnl'])
    print(f"\n{'тикер':<6}{'сдел':>6}{'сум₽':>12}{'WR%':>7}")
    for tk in sorted(by_tk, key=lambda x: -sum(by_tk[x])):
        p = np.array(by_tk[tk])
        print(f"{tk:<6}{len(p):>6}{p.sum():>12,.0f}{(p>0).mean()*100:>7.1f}")
    ch.close()
