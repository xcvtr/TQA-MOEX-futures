#!/usr/bin/env python3 -u
"""OI редкие сделки + профи-трюки: пирамидинг, замок, реинвест.

Портфель 6 тикеров: BR, NG, SV, RN, GZ, Eu.
Сигнал: накопленный day_net <= -8 → long, выход +1д (или замок).
Трюки:
  - reinvest: risk % от текущего капитала (растёт с прибылью)
  - pyramiding: вход 1 лот, +1 лот если цена прошла +pyra_ticks тиков в плюс
  - lock: при достижении lock_ticks тиков профита — фиксируем (стоп в безубыток/частичный)
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
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
                  f"WHERE ticker='{MT[tk]}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, c) for ts, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    prices = (arr[o, 0], arr[o, 1])
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

def gen_signals(years, thr=8):
    """Дневные сигналы: (tk, day, entry_ts, entry_prc, exit_prc на +1д, spec)."""
    sigs = []
    for tk in TICKERS:
        for y in years:
            d = load_tk(tk, y)
            if d is None: continue
            net_map, prices, spec = d
            pts, pprc = prices
            go, ms, sp, fee = spec
            day_best = {}
            for ts in sorted(net_map.keys()):
                dn = net_map[ts]
                if dn > -thr: continue
                idx = bisect.bisect_right(pts, ts) - 1
                if idx < 0: continue
                prc = pprc[idx]
                if prc <= 0 or (ts - pts[idx]) > 600: continue
                cutoff = (int((ts - TZ_SHIFT)//86400)+1)*86400 - TZ_SHIFT
                j = bisect.bisect_left(pts, cutoff + 86400)
                if j >= len(pts): continue
                exit_p = pprc[j]
                day = int((ts - TZ_SHIFT)//86400)
                if day not in day_best or abs(dn) > abs(day_best[day]['dn']):
                    day_best[day] = {'ts': ts, 'exit_p': exit_p, 'prc': prc,
                                     'ms': ms, 'sp': sp, 'fee': fee, 'go': go, 'dn': dn}
            for day, t in day_best.items():
                t['tk'] = tk; t['day'] = day
                sigs.append(t)
    return sigs

def run(sigs, years, risk=0.10, pyramiding=0, lock_ticks=0):
    """pyramiding: число доп. лотов; lock_ticks: фиксация при профите N тиков."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    for t in sorted(sigs, key=lambda x: x['ts']):
        base_lots = max(1, int(eq * risk / t['go']))
        lots = base_lots
        # пирамидинг: если движение в плюс на pyra_ticks → +1 лот (приблизительно: exit уже финальный)
        if pyramiding > 0:
            ticks_move = (t['exit_p'] - t['prc']) / t['ms']
            if ticks_move > 0:
                add = min(pyramiding, int(ticks_move / 20))  # каждые 20 тиков +1 лот
                lots += add
        pnl = ((t['exit_p'] - t['prc']) / t['ms'] * t['sp'] - t['fee'] * 2) * lots
        eq += pnl
        n += 1
        if pnl > 0: wins += 1
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    per_year = n / len(years)
    return {'n': n, 'per_year': round(per_year,1), 'roi': round((eq-200000)/200000*100,1),
            'mdd': round(mdd,1), 'wr': round(wins/n*100,1) if n else 0}

years = [2022, 2023, 2024, 2025, 2026]
sigs = gen_signals(years)
print(f"Сигналов: {len(sigs)} = {len(sigs)/len(years):.0f}/год")

print(f"\n{'конфиг':<30}{'сдел/год':<9}{'ROI%':>10}{'MDD%':>8}{'WR%':>7}")
print("-" * 66)
for risk in [0.05, 0.10, 0.15]:
    for pyr in [0, 1, 3]:
        for lock in [0, 50]:
            r = run(sigs, years, risk=risk, pyramiding=pyr, lock_ticks=lock)
            name = f"risk{risk:.0%} pyr{pyr} lock{lock}"
            print(f"{name:<30}{r['per_year']:<9.0f}{r['roi']:>+10.1f}{r['mdd']:>8.1f}{r['wr']:>7.1f}")
    print()
ch.close()
