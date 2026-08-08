#!/usr/bin/env python3 -u
"""Честный компаунд + пирамидинг по барам (редкие крупные).

Позиция открыта по сигналу (day_net <= -8), управляется по M1 барам до +1д:
  - добавка: когда hi бара >= fill + k*pyra_ticks → +1 лот по цене hi (реальный вход)
  - выход: close последнего бара окна (+1д), или стоп
Компаунд: risk % от текущего equity на каждую часть.

Это честнее предыдущего (добавки не «по идеалу», а по реальному движению баров).
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600
VALID = {'BR':'BR', 'NG':'NG', 'SV':'SILV', 'RN':'RN', 'GZ':'GZ', 'Eu':'Eu',
         'RI':'RTSI', 'LK':'LKOH', 'SN':'SNGP', 'SP':'SBRF', 'MG':'MGNT',
         'VB':'VTBR', 'TT':'TATN', 'AF':'AFLT', 'HY':'HYDR'}

def load_tk(fut_tk, mt_tk, y, spec):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch.query(f"SELECT bt, buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi "
                 f"WHERE ticker='{fut_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    day_start = {}
    net_map = {}
    for bt, fb, fs, yb, ys in r:
        ts = bt.replace(tzinfo=None).timestamp() + TZ_SHIFT
        d = int((ts - TZ_SHIFT) // 86400)
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
    return net_map, arr[o], spec

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
specs = {}
for t, go, ms, sp, fee in cur.fetchall():
    specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

def gen_entries(years, thr=8):
    entries = []
    for fut_tk, mt_tk in VALID.items():
        spec = specs.get(mt_tk)
        if spec is None: continue
        for y in years:
            d = load_tk(fut_tk, mt_tk, y, spec)
            if d is None: continue
            net_map, bars, spc = d
            pts = bars[:, 0]
            go, ms, sp, fee = spc
            day_best = {}
            for ts in sorted(net_map.keys()):
                dn = net_map[ts]
                if dn > -thr: continue
                idx = bisect.bisect_right(pts, ts) - 1
                if idx < 0: continue
                prc = bars[idx, 4]
                if prc <= 0 or (ts - pts[idx]) > 600: continue
                day = int((ts - TZ_SHIFT)//86400)
                if day not in day_best or abs(dn) > abs(day_best[day]['dn']):
                    day_best[day] = {'ts': ts, 'prc': prc, 'ms': ms, 'sp': sp,
                                     'fee': fee, 'go': go, 'dn': dn}
            for day, t in day_best.items():
                t['tk'] = fut_tk; t['bars'] = bars
                entries.append(t)
    return entries

def run(entries, years, risk=0.10, pyr_max=1, pyra_ticks=20, stop_ticks=200):
    """Честный: добавки по hi баров, выход close +1д, компаунд."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    for e in sorted(entries, key=lambda x: x['ts']):
        ms = e['ms']; sp = e['sp']; fee = e['fee']; go = e['go']
        entry_ts = e['ts']
        bars = e['bars']
        pts = bars[:, 0]
        day_end = int((entry_ts - TZ_SHIFT)//86400) + 2
        cutoff = day_end * 86400 - TZ_SHIFT
        i0 = bisect.bisect_right(pts, entry_ts) - 1
        i1 = bisect.bisect_right(pts, cutoff) - 1
        if i1 <= i0 or i1 >= len(bars): continue
        window = bars[i0:i1+1]
        fill_p = e['prc'] + ms  # вход +1 тик
        # части позиции: (лоты, цена входа)
        base_lots = max(1, int(eq * risk / go))
        parts = [(base_lots, fill_p)]
        n_parts = 1
        exit_p = None
        for bar in window:
            hi = bar[2]; lo = bar[3]
            # пирамидинг: если hi прошёл +k*pyra_ticks → добавить часть
            if n_parts < pyr_max:
                ticks_up = (hi - fill_p) / ms
                target = n_parts  # уже есть n_parts частей, хотим n_parts+1 при достижении n_parts*pyra_ticks
                if ticks_up >= target * pyra_ticks:
                    add_price = hi + ms  # вход добавки по текущей цене + тик
                    parts.append((base_lots, add_price))
                    n_parts += 1
            # стоп (страховочный, широкий)
            if lo <= fill_p - ms * stop_ticks:
                exit_p = fill_p - ms * stop_ticks
                break
        if exit_p is None:
            exit_p = window[-1, 4] - ms  # выход close +1д, минус тик
        pnl = 0.0
        for lots, p_in in parts:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl; n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    per_year = n / len(years)
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'wr': round(wins/n*100,1) if n else 0}

years = [2022, 2023, 2024, 2025, 2026]
entries = gen_entries(years)
print(f"Сигналов: {len(entries)} = {len(entries)/len(years):.0f}/год")

print(f"\n{'конфиг':<24}{'сдел/год':<9}{'ROI%':>11}{'MDD%':>8}{'WR%':>7}{'CAGR%':>8}")
print("-" * 68)
for risk in [0.05, 0.10, 0.15, 0.20]:
    for pyr in [1, 2, 3, 5]:
        r = run(entries, years, risk=risk, pyr_max=pyr)
        cagr = ((1 + r['roi']/100) ** (1/5) - 1) * 100 if r['roi'] > -100 else -100
        print(f"risk{risk:.0%} pyr{pyr}  {r['per_year']:<9.0f}{r['roi']:>+11.1f}"
              f"{r['mdd']:>8.1f}{r['wr']:>7.1f}{cagr:>8.1f}")
ch.close()
