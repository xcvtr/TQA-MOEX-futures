#!/usr/bin/env python3 -u
"""OI v4 — полный арсенал кванта.

Вход:    day_net <= -thr (накопленная паника физ за день), max|dn| за день
Пирамид: добавка при +pyra_pct% от входа (нормировано по цене!), до pyr_max частей
Трейлинг: после активации (trail_act_pct%) стоп следует за hi на trail_pct%
Замок:   при +lock_pct% фиксируем 40% позиции, стоп в безубыток
DD-контроль: риск ×0.5 при DD>10%, ×0.25 при DD>20% (до нового пика)
Компаунд: risk % от текущего equity
Выход:   трейлинг/стоп/макс-время (max_hold_ч)

Чистые таймзоны: UTC-epoch, день = 15:00 IRK.
"""
import sys, bisect, argparse
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
DAY_SEC = 86400

# Ядро (подтверждённые): NG BR SV TT RI
ALL = {
 'BR': ('BR', True), 'NG': ('NG', True), 'SV': ('SILV', True),
 'RI': ('RTSI', True), 'TT': ('TATN', True),
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

def gen_signals(years, thr):
    sigs = []
    for fut_tk, (mt_tk, on) in ALL.items():
        if not on: continue
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

def backtest(sigs, years, risk, slip=1,
             pyr=3, pyra_pct=1.0,        # пирамидинг: % от цены входа
             trail_act=1.5, trail_pct=0.8,  # трейлинг: активация %, откат %
             lock_pct=3.0,                # замок: при % фиксируем 40%
             max_hold_h=72,               # максимум держим (часов)
             dd_control=True):            # DD-контроль риска
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    trades = []
    for t in sorted(sigs, key=lambda x: x['ts']):
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['ts']) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip
        # DD-контроль
        cur_dd = (peak - eq) / peak * 100 if peak > 0 else 0
        risk_mult = 1.0
        if dd_control:
            if cur_dd > 20: risk_mult = 0.25
            elif cur_dd > 10: risk_mult = 0.5
        # позиция: части (лоты, цена)
        base_lots = max(1, int(eq * risk * risk_mult / go))
        parts = [(base_lots, fill_p)]
        # окно: от входа до max_hold
        i_max = bisect.bisect_right(pts, t['ts'] + max_hold_h * 3600)
        if i_max <= i0: continue
        window = bars[i0:i_max+1]
        # управление
        max_hi = fill_p
        stop = fill_p * (1 - 0.02)  # начальный страховочный стоп 2%
        locked = False
        realized = 0.0
        exit_p = None
        n_adds = 0
        for bar in window:
            ts_b, opn, hi, lo, cl = bar[0], bar[1], bar[2], bar[3], bar[4]
            max_hi = max(max_hi, hi)
            # пирамидинг: при +n*pyra_pct% от входа
            if n_adds < pyr - 1:
                gain_pct = (hi - fill_p) / fill_p * 100
                target = n_adds + 1
                if gain_pct >= target * pyra_pct:
                    parts.append((base_lots, hi + ms * slip))
                    n_adds += 1
            # замок: при +lock_pct% фиксируем 40%
            if not locked and (max_hi - fill_p) / fill_p * 100 >= lock_pct:
                locked = True
                fixed_lots = int(len(parts) * 0.4)  # 40% частей
                if fixed_lots > 0:
                    for k in range(fixed_lots):
                        lots, p_in = parts[k]
                        realized += ((max_hi - p_in) / ms * sp - fee * 2) * lots
                    parts = parts[fixed_lots:]
                stop = fill_p  # стоп в безубыток
            # трейлинг: после активации
            if (max_hi - fill_p) / fill_p * 100 >= trail_act:
                stop = max(stop, max_hi * (1 - trail_pct / 100))
            # стоп
            if lo <= stop:
                exit_p = stop
                break
        if exit_p is None:
            exit_p = window[-1, 4] - ms * slip
        # PnL оставшихся частей
        pnl = realized
        for lots, p_in in parts:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        trades.append({'tk': t['tk'], 'pnl': pnl, 'parts': len(parts) + (0 if not locked else 0)})
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
    ap.add_argument('--risk', type=float, default=0.15)
    ap.add_argument('--slip', type=int, default=1)
    ap.add_argument('--pyr', type=int, default=3)
    ap.add_argument('--pyra-pct', type=float, default=1.0)
    ap.add_argument('--trail-act', type=float, default=1.5)
    ap.add_argument('--trail-pct', type=float, default=0.8)
    ap.add_argument('--lock-pct', type=float, default=3.0)
    ap.add_argument('--max-hold', type=float, default=72)
    ap.add_argument('--no-dd', action='store_true')
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
    r = backtest(sigs, years, args.risk, slip=args.slip,
                 pyr=args.pyr, pyra_pct=args.pyra_pct,
                 trail_act=args.trail_act, trail_pct=args.trail_pct,
                 lock_pct=args.lock_pct, max_hold_h=args.max_hold,
                 dd_control=not args.no_dd)
    print(f"Сигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год (thr={args.thr})")
    print(f"risk={args.risk:.0%} pyr={args.pyr} pyra={args.pyra_pct}% trail={args.trail_act}/{args.trail_pct}% "
          f"lock={args.lock_pct}% hold={args.max_hold}ч dd_ctrl={not args.no_dd}")
    print(f"ИТОГ: ROI {r['roi']:+.1f}% за 5 лет, CAGR {r['cagr']:.1f}%, MDD {r['mdd']:.1f}%, "
          f"WR {r['wr']:.1f}%, {r['n']} сделок ({r['per_year']:.0f}/год)")
    by_tk = {}
    for t in r['trades']:
        by_tk.setdefault(t['tk'], []).append(t['pnl'])
    print(f"\n{'тикер':<6}{'сдел':>6}{'сум₽':>12}{'WR%':>7}")
    for tk in sorted(by_tk, key=lambda x: -sum(by_tk[x])):
        p = np.array(by_tk[tk])
        print(f"{tk:<6}{len(p):>6}{p.sum():>12,.0f}{(p>0).mean()*100:>7.1f}")
    ch.close()
