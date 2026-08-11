#!/usr/bin/env python3 -u
"""Валидация честного портфеля: OOS по годам + avg PnL/сделку.

Проверяем: не артефакт ли +4676% (risk10% pyr3).
- По годам: стабилен ли edge или 1 год тащит
- Avg PnL на сделку в % капитала: реалистично?
- Один тикер не даёт ли всё?
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
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), prc FROM moex.mt5_continuous "
                  f"WHERE ticker='{mt_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts, c) for ts, c in r2 if c and c > 0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:, 0])
    return net_map, (arr[o, 0], arr[o, 1]), spec

pg = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = pg.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
specs = {}
for t, go, ms, sp, fee in cur.fetchall():
    specs[t] = (float(go), float(ms), float(sp), float(fee))
pg.close()

def gen_signals(years, thr=8):
    sigs = []
    for fut_tk, mt_tk in VALID.items():
        spec = specs.get(mt_tk)
        if spec is None: continue
        for y in years:
            d = load_tk(fut_tk, mt_tk, y, spec)
            if d is None: continue
            net_map, prices, spc = d
            pts, pprc = prices
            go, ms, sp, fee = spc
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
                    day_best[day] = {'ts': ts, 'exit_p': exit_p, 'prc': prc, 'ms': ms,
                                     'sp': sp, 'fee': fee, 'go': go, 'dn': dn}
            for day, t in day_best.items():
                t['tk'] = fut_tk
                # год сделки
                import datetime as dt
                t['y'] = dt.datetime.fromtimestamp(t['ts'] - TZ_SHIFT).year
                sigs.append(t)
    return sigs

years = [2022, 2023, 2024, 2025, 2026]
sigs = gen_signals(years)

# 1. По годам: суммарный PnL (1 лот на сделку — без риска/компаунда, честно)
print("=== PnL по годам (1 лот/сделку, без компаунда) ===")
print(f"{'год':<6}{'сдел':>6}{'сум₽':>12}{'avg₽':>9}{'WR%':>7}")
by_year = {}
for s in sigs:
    by_year.setdefault(s['y'], []).append(s)
for y in sorted(by_year):
    ss = by_year[y]
    pnls = [((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in ss]
    print(f"{y:<6}{len(ss):>6}{sum(pnls):>12,.0f}{np.mean(pnls):>9,.0f}{(np.array(pnls)>0).mean()*100:>7.1f}")

# 2. По тикерам
print(f"\n=== PnL по тикерам (1 лот, 5 лет) ===")
print(f"{'тикер':<6}{'сдел':>6}{'сум₽':>12}{'avg₽':>9}{'WR%':>7}")
by_tk = {}
for s in sigs:
    by_tk.setdefault(s['tk'], []).append(s)
for tk, ss in sorted(by_tk.items()):
    pnls = [((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in ss]
    print(f"{tk:<6}{len(ss):>6}{sum(pnls):>12,.0f}{np.mean(pnls):>9,.0f}{(np.array(pnls)>0).mean()*100:>7.1f}")

# 3. Портфель без компаунда (фиксированный риск на сделку 10% от 200K)
print(f"\n=== Портфель: фикс риск 10% от стартового 200K (без реинвеста) ===")
eq = 200000.0
pnls_all = []
for t in sorted(sigs, key=lambda x: x['ts']):
    lots = max(1, int(200000 * 0.10 / t['go']))
    fill_p = t['prc'] + t['ms']
    exit_p = t['exit_p'] + t['ms']
    pnl = ((exit_p - fill_p) / t['ms'] * t['sp'] - t['fee'] * 2) * lots
    eq += pnl
    pnls_all.append(pnl)
print(f"ROI без компаунда: {(eq-200000)/200000*100:+.1f}% за 5 лет ({(eq/200000)**(1/5)-1:.1%}/год CAGR)")
print(f"avg PnL/сделку: {np.mean(pnls_all):+,.0f}₽ = {np.mean(pnls_all)/200000*100:.2f}% капитала")
print(f"WR: {(np.array(pnls_all)>0).mean()*100:.1f}%")

# 4. Проверка: не один ли тикер даёт всё
print(f"\n=== Вклад тикеров в суммарный PnL ===")
total = sum(((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in sigs)
for tk, ss in sorted(by_tk.items(), key=lambda x: -sum(((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in x[1])):
    p = sum(((t['exit_p']-t['prc'])/t['ms']*t['sp'] - t['fee']*2) for t in ss)
    print(f"{tk:<6} {p:>12,.0f}  {p/total*100:>5.1f}%")
ch.close()
