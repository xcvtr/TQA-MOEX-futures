#!/usr/bin/env python3 -u
"""OI v7: управление сделками при WR 80%.

Вопрос: при WR 80% (сигнал почти всегда прибыльный) — как лучше выходить?
1. Горизонт: 24ч vs 48ч vs 72ч vs 5д vs 10д
2. Замок: фикс 30-50% на +2/+3/+5%, остальное до конца горизонта
3. Трейлинг широкий (3-5% откат) после активации
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

def backtest(sigs, years, risk_map, slip=1, pyr=3, pyra_pct=0.5,
             horizon_h=24, lock_pct=0, lock_frac=0.0, trail_after=None, trail_pct=5.0):
    """Управление:
    - horizon_h: макс держим (часы)
    - lock_pct: при +lock_pct% фиксируем lock_frac долю (по цене того момента), остальное держим
    - trail_after: после +trail_after% включаем трейлинг с откатом trail_pct%
    """
    eq = 200000.0; peak_cash = eq; peak_mtm = eq
    cash_mdd = mtm_mdd = 0.0
    n = 0; wins = 0
    eq_by_year = {}
    for t in sorted(sigs, key=lambda x: x['ts']):
        ms = t['ms']; sp = t['sp']; fee = t['fee']; go = t['go']
        bars = t['bars']; pts = bars[:, 0]
        i0 = bisect.bisect_right(pts, t['ts']) - 1
        if i0 < 0: continue
        fill_p = bars[i0, 4] + ms * slip
        risk = risk_map.get(t['tk'], 0.10)
        base_lots = max(1, int(eq * risk / go))
        parts = [(base_lots, fill_p)]
        if pyr > 1:
            i_max = bisect.bisect_right(pts, t['ts'] + horizon_h * 3600)
            for k in range(1, pyr):
                level = fill_p * (1 + k * pyra_pct / 100)
                found = False
                for bi in range(i0, min(i_max, len(bars))):
                    if bars[bi, 2] >= level:
                        parts.append((base_lots, bars[bi, 2] + ms * slip))
                        found = True
                        break
                if not found:
                    break
        # проход по барам с управлением
        i_max = bisect.bisect_right(pts, t['ts'] + horizon_h * 3600)
        if i_max <= i0: continue
        exit_p = None
        realized = 0.0
        remaining = list(parts)
        max_hi = fill_p
        trail_active = False
        trail_stop = 0.0
        for bi in range(i0, min(i_max, len(bars))):
            hi = bars[bi, 2]; lo = bars[bi, 3]
            max_hi = max(max_hi, hi)
            # замок: при +lock_pct% фиксируем долю
            if lock_pct > 0 and remaining and (max_hi - fill_p) / fill_p * 100 >= lock_pct:
                n_lock = max(1, int(len(remaining) * lock_frac))
                for k in range(n_lock):
                    lots, p_in = remaining[0]
                    realized += ((max_hi - p_in) / ms * sp - fee * 2) * lots
                    remaining = remaining[1:]
                if not remaining:
                    exit_p = max_hi
                    break
            # трейлинг
            if trail_after is not None and not trail_active:
                if (max_hi - fill_p) / fill_p * 100 >= trail_after:
                    trail_active = True
            if trail_active:
                trail_stop = max(trail_stop, max_hi * (1 - trail_pct / 100))
                if lo <= trail_stop:
                    exit_p = trail_stop
                    break
        # MTM на каждом баре (внутри позиции): equity до сделки + текущий mtm по открытым частям
        eq_before = eq  # equity до закрытия этой сделки
        for bi in range(i0, min(i_max, len(bars))):
            lo = bars[bi, 3]
            mtm_pnl = realized
            for lots, p_in in remaining:
                mtm_pnl += ((lo - p_in) / ms * sp - fee * 2) * lots
            mtm_eq = eq_before + mtm_pnl
            peak_mtm = max(peak_mtm, mtm_eq)
            mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100 if peak_mtm > 0 else 0)
        if exit_p is None:
            exit_p = bars[min(i_max, len(bars))-1, 4] - ms * slip
        pnl = realized
        for lots, p_in in remaining:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak_cash = max(peak_cash, eq)
        cash_mdd = max(cash_mdd, (peak_cash - eq) / peak_cash * 100)
        # MTM: худший lo в окне для всех частей
        for bi in range(i0, min(i_max, len(bars))):
            lo = bars[bi, 3]
            mtm_pnl = 0.0
            for lots, p_in in parts:
                mtm_pnl += ((lo - p_in) / ms * sp - fee * 2) * lots
            mtm_eq = (eq - pnl) + mtm_pnl
            peak_mtm = max(peak_mtm, mtm_eq)
            mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100 if peak_mtm > 0 else 0)
        # eq на конец года сделки
        y = datetime.fromtimestamp(t['ts'], tz=timezone.utc).year
        eq_by_year[y] = eq
    per_year = n / len(years)
    cagr = ((1 + (eq-200000)/200000) ** (1/len(years)) - 1) * 100 if eq > 0 else -100
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'cash_mdd': round(cash_mdd,1), 'mtm_mdd': round(mtm_mdd,1),
            'wr': round(wins/n*100,1) if n else 0, 'cagr': round(cagr,1),
            'eq_by_year': eq_by_year}

if __name__ == '__main__':
    pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
    cur = pg.cursor()
    cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
    global specs
    specs = {}
    for t, go, ms, sp, fee in cur.fetchall():
        specs[t] = (float(go), float(ms), float(sp), float(fee))
    pg.close()

    years = [2022, 2023, 2024, 2025, 2026]
    sigs = gen_signals(years, 8)
    risk_map = {'BR': 0.15, 'NG': 0.15, 'SV': 0.10, 'RI': 0.10, 'TT': 0.10}
    print(f"Сигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год")
    print(f"\n{'конфиг':<32}{'CAGR%':>8}{'CashMDD':>9}{'MTM MDD':>9}{'WR%':>7}")
    print("-" * 66)
    cfgs = [
        ("24ч (база)", dict(horizon_h=24)),
        ("48ч", dict(horizon_h=48)),
        ("72ч", dict(horizon_h=72)),
        ("5д (120ч)", dict(horizon_h=120)),
        ("10д (240ч)", dict(horizon_h=240)),
        ("48ч + lock30% на +3%", dict(horizon_h=48, lock_pct=3, lock_frac=0.3)),
        ("72ч + lock40% на +3%", dict(horizon_h=72, lock_pct=3, lock_frac=0.4)),
        ("120ч + lock40% на +5%", dict(horizon_h=120, lock_pct=5, lock_frac=0.4)),
        ("72ч + trail 5% после +5%", dict(horizon_h=72, trail_after=5, trail_pct=5)),
        ("72ч + trail 3% после +3%", dict(horizon_h=72, trail_after=3, trail_pct=3)),
        ("120ч + trail 7% после +7%", dict(horizon_h=120, trail_after=7, trail_pct=7)),
    ]
    for name, kw in cfgs:
        r = backtest(sigs, years, risk_map, pyr=3, pyra_pct=0.5, **kw)
        print(f"{name:<32}{r['cagr']:>8.1f}{r['cash_mdd']:>9.1f}{r['mtm_mdd']:>9.1f}{r['wr']:>7.1f}")
    ch.close()
