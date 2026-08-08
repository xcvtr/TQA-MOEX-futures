#!/usr/bin/env python3 -u
"""Настоящий lock: пирамидинг + замок с трекингом цены внутри дня.

Вход: накопленный day_net <= -8 → long в день D.
Управление: в течение дня D+1 (до выхода) следим за ценой:
  - pyramiding: при +N тиков от входа → +1 лот (добавка)
  - lock: при +lock_ticks от входа → стоп перемещается в безубыток (breakeven),
          при +lock_ticks*2 → фиксируем 50% (замок), остальное трейлим
  - трейлинг: стоп следует за ценой на trail_ticks от максимума
Выход: конец дня D+1 или срабатывание стопа/трейлинга.
"""
import sys, bisect
import numpy as np
import clickhouse_connect as cc, psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
TZ_SHIFT = 5 * 3600
MT = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV', 'RN': 'RN', 'GZ': 'GZ', 'Eu': 'Eu'}
TICKERS = ['BR', 'NG', 'SV', 'RN', 'GZ', 'Eu']

def load_tk(tk, y):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-07'
    r = ch.query(f"SELECT bt, buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi "
                 f"WHERE ticker='{tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
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
                  f"WHERE ticker='{MT[tk]}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, o, h, l, c) for ts, o, h, l, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    prices = arr[o]
    try:
        pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
        cur = pg.cursor()
        cur.execute("SELECT go, min_step, step_price, fee_entry FROM futures.ticker_specs WHERE ticker=%s", (tk,))
        row = cur.fetchone(); pg.close()
        if row is None: return None
        spec = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
    except Exception:
        return None
    return net_map, prices, spec

def gen_entries(years, thr=8):
    """Сигналы: (tk, entry_ts, entry_prc, spec, prices) — один на день. Данные грузим 1 раз."""
    entries = []
    for tk in TICKERS:
        all_net = {}
        all_prices = []
        all_spec = None
        for y in years:
            d = load_tk(tk, y)
            if d is None: continue
            net_map, prices, spec = d
            all_net.update(net_map)
            all_prices.append(prices)
            all_spec = spec
        if not all_prices or all_spec is None:
            continue
        prices_all = np.concatenate(all_prices)
        o = np.argsort(prices_all[:, 0])
        prices_all = prices_all[o]
        pts = prices_all[:, 0]
        go, ms, sp, fee = all_spec
        day_best = {}
        for ts in sorted(all_net.keys()):
            dn = all_net[ts]
            if dn > -thr: continue
            idx = bisect.bisect_right(pts, ts) - 1
            if idx < 0: continue
            prc = prices_all[idx, 4]
            if prc <= 0 or (ts - pts[idx]) > 600: continue
            day = int((ts - TZ_SHIFT)//86400)
            if day not in day_best or abs(dn) > abs(day_best[day]['dn']):
                day_best[day] = {'ts': ts, 'prc': prc, 'ms': ms, 'sp': sp,
                                 'fee': fee, 'go': go, 'dn': dn}
        for day, t in day_best.items():
            t['tk'] = tk; t['day'] = day; t['prices'] = prices_all
            entries.append(t)
    return entries

def run_with_lock(entries, years, risk=0.10, pyr_max=3, pyra_ticks=20,
                  lock_ticks=0, trail_ticks=30):
    """Позиция открыта в день D (после сигнала), управляется до конца дня D+1."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    for e in sorted(entries, key=lambda x: x['ts']):
        ms = e['ms']; sp = e['sp']; fee = e['fee']; go = e['go']
        entry_p = e['prc']; entry_ts = e['ts']
        prices_all = e['prices']
        pts = prices_all[:, 0]
        day_end = int((entry_ts - TZ_SHIFT)//86400) + 2
        cutoff = day_end * 86400 - TZ_SHIFT
        i0 = bisect.bisect_right(pts, entry_ts) - 1
        i1 = bisect.bisect_right(pts, cutoff) - 1
        if i1 <= i0 or i1 >= len(prices_all): continue
        window = prices_all[i0:i1+1]
        lots = max(1, int(eq * risk / go))
        total_lots = lots
        fill_p = entry_p + ms  # slippage 1 тик
        stop = fill_p - ms * 100
        locked = False
        realized = 0.0
        remaining_lots = total_lots
        exit_p = None
        for bar in window:
            ts_b, opn, hi, lo, cl = bar[0], bar[1], bar[2], bar[3], bar[4]
            if pyr_max > 1 and not locked:
                gain_ticks = (hi - fill_p) / ms
                target_adds = int(gain_ticks / pyra_ticks)
                cur_adds = (total_lots - lots) // lots
                while cur_adds < target_adds and cur_adds < pyr_max - 1:
                    total_lots += lots
                    cur_adds += 1
                    remaining_lots += lots
            if lock_ticks > 0 and not locked:
                if (hi - fill_p) / ms >= lock_ticks:
                    locked = True
                    fixed = remaining_lots // 2
                    if fixed > 0:
                        remaining_lots -= fixed
                    stop = fill_p
            if locked or trail_ticks > 0:
                if (hi - fill_p) / ms >= trail_ticks:
                    stop = max(stop, hi - ms * trail_ticks)
            if lo <= stop:
                exit_p = stop
                break
        if exit_p is None:
            exit_p = window[-1, 4]
        pnl = ((exit_p - fill_p) / ms * sp - fee * 2) * remaining_lots + realized
        eq += pnl
        n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    per_year = n / len(years)
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'wr': round(wins/n*100,1) if n else 0}

years = [2022, 2023, 2024, 2025, 2026]
entries = gen_entries(years)
print(f"Сигналов: {len(entries)} = {len(entries)/len(years):.0f}/год")

print(f"\n{'конфиг':<34}{'сдел/год':<9}{'ROI%':>10}{'MDD%':>8}{'WR%':>7}")
print("-" * 70)
for risk in [0.05, 0.10, 0.15]:
    for pyr in [1, 3]:
        for lock in [0, 40]:
            r = run_with_lock(entries, years, risk=risk, pyr_max=pyr, pyra_ticks=20,
                              lock_ticks=lock, trail_ticks=30)
            name = f"risk{risk:.0%} pyr{pyr} lock{lock}"
            print(f"{name:<34}{r['per_year']:<9.0f}{r['roi']:>+10.1f}{r['mdd']:>8.1f}{r['wr']:>7.1f}")
    print()
ch.close()
