#!/usr/bin/env python3 -u
"""Честный пирамидинг: добавка по текущей цене, выход +1д, без трейлинга.

pro_tricks дал +1013% но нечестно (все лоты по exit).
lock_backtest честно но с трейлингом (режет движение).

Здесь: вход base лотов по fill, при +pyra_ticks → +1 лот по цене добавки (hi),
все выходят по close +1д. Пирамидинг увеличивает средний лот только в прибыльных.
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
        if not all_prices or all_spec is None: continue
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

def run_honest(entries, years, risk=0.10, pyr_max=1, pyra_ticks=20):
    """Честный пирамидинг: base лотов по fill, добавки по hi при +20 тиков, выход +1д close."""
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
        base_lots = max(1, int(eq * risk / go))
        fill_p = entry_p + ms
        # позиция: список (лоты, цена входа)
        pos = [(base_lots, fill_p)]
        total_lots = base_lots
        added = 0
        for bar in window:
            ts_b, opn, hi, lo, cl = bar[0], bar[1], bar[2], bar[3], bar[4]
            # пирамидинг: при +pyra_ticks от первоначального входа → +1 лот по текущей цене
            if added < pyr_max - 1:
                gain_ticks = (hi - fill_p) / ms
                target_adds = int(gain_ticks / pyra_ticks)
                while added < target_adds and added < pyr_max - 1:
                    add_price = hi + ms  # slippage на добавке
                    pos.append((base_lots, add_price))
                    total_lots += base_lots
                    added += 1
        # выход по close конца окна (+1д)
        exit_p = window[-1, 4]
        # PnL по каждой части
        pnl = 0.0
        for lots, p_in in pos:
            pnl += ((exit_p - p_in) / ms * sp - fee * 2) * lots
        eq += pnl
        n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    per_year = n / len(years)
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'wr': round(wins/n*100,1) if n else 0,
            'avg_adds': round(np.mean([1 + min((t.get('dn')*-1)/0 if False else 0, 2)]),2) if False else 0}

years = [2022, 2023, 2024, 2025, 2026]
entries = gen_entries(years)
print(f"Сигналов: {len(entries)} = {len(entries)/len(years):.0f}/год")

print(f"\n{'конфиг':<26}{'сдел/год':<9}{'ROI%':>10}{'MDD%':>8}{'WR%':>7}")
print("-" * 62)
for risk in [0.05, 0.10, 0.15, 0.20]:
    for pyr in [1, 2, 3, 5]:
        r = run_honest(entries, years, risk=risk, pyr_max=pyr, pyra_ticks=20)
        name = f"risk{risk:.0%} pyr{pyr}"
        print(f"{name:<26}{r['per_year']:<9.0f}{r['roi']:>+10.1f}{r['mdd']:>8.1f}{r['wr']:>7.1f}")
    print()
ch.close()
