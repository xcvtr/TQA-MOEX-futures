#!/usr/bin/env python3 -u
"""Пошаговый компаунд OI: lots = risk*eq/go КАЖДЫЙ тик (как папер реально),
slippage из реальной глубины стакана растёт с размером.
Показывает точную кривую eq до упора в ликвидность."""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc, numpy as np, bisect
from datetime import datetime, timezone
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def irk_day(ts): return int((ts - 7*3600)//86400)
specs = {
    'BR':(27606,0.01,7.706110,4.0),'NG':(6093,0.001,7.706110,4.0),  # ПГО!
    'SV':(10971,0.01,7.706110,4.0),
}
RISKS = {'BR': 0.15, 'NG': 0.10, 'SV': 0.05}
DEPTH = {'BR': (13, 1.28), 'NG': (88, 1.14), 'SV': (18, 1.29)}  # lots = a*n^b
DAILY5 = {'BR': 609402, 'NG': 401991, 'SV': 349493}
LIMITS = {k: max(10, int(v*0.05)) for k,v in DAILY5.items()}

def slippage_ticks(tk, lots):
    a, b = DEPTH[tk]
    n = (lots / a) ** (1/b) if lots > 0 else 1
    return max(1, int(np.ceil(n)))

def live_ok(ts):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    msk_h = (dt.hour + 3) % 24
    return msk_h >= 10 or msk_h < 2

DATA = {}
for y in [2023,2024,2025,2026]:
    END = '2026-08-09' if y == 2026 else f'{y}-12-31'
    for fut_tk in ['BR','NG','SV']:
        r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur FROM moex.futoi WHERE ticker='{fut_tk}' AND bt>='{y}-01-01' AND bt<='{END} 23:59:59'").result_rows
        day_start = {}; net_map = {}
        for ts, fb, fs, yb, ys in r:
            d = irk_day(ts)
            if d not in day_start: day_start[d] = int(fb)-int(fs)
            total = int(fb)+int(fs)+int(yb)+int(ys)
            if total <= 0: continue
            net_map[ts] = (int(fb)-int(fs)-day_start[d])/total*100
        r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc FROM moex.mt5_continuous WHERE ticker='{fut_tk}' AND bt>='{y}-01-01' AND bt<='{END} 23:59:59'").result_rows
        arr = np.array([(ts,o,h,l,c) for ts,o,h,l,c in r2 if c and c>0], dtype=np.float64)
        arr = arr[np.argsort(arr[:,0])]
        DATA[(y,fut_tk)] = (net_map, arr)
    print(f'loaded {y}', flush=True)

def run_stepwise(years=[2023,2024,2025,2026], thr=3, exit_thr=1.5, pyr=5, pyra_pct=0.3, stop_pct=1.5):
    """lots от ТЕКУЩЕГО eq каждый вход (реальный компаунд), slippage растёт."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0; slips = []
    year_eq = {}
    max_lots_seen = 0
    for y in years:
        for tk in ['BR','NG','SV']:
            risk = RISKS[tk]
            net_map, bars = DATA[(y,tk)]
            go, ms, sp, fee = specs[tk]
            max_lots = LIMITS[tk]
            pts = bars[:,0]
            fts = sorted(ts for ts, dn in net_map.items() if abs(dn) >= min(thr, exit_thr) - 0.5 and live_ok(ts))
            for direction in ['long','short']:
                pos = None
                for ts in fts:
                    dn = net_map[ts]
                    if pos is not None:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        cur_p = bars[idx,4]; lo = bars[idx,3]; hi = bars[idx,2]
                        exit_cond = (dn >= exit_thr) if direction=='long' else (dn <= -exit_thr)
                        hold_h = (ts - pos['entry_ts'])/3600
                        stop_hit = False
                        if stop_pct is not None:
                            if direction=='long' and lo <= pos['entry_p']*(1-stop_pct/100): stop_hit = True
                            if direction=='short' and hi >= pos['entry_p']*(1+stop_pct/100): stop_hit = True
                        if exit_cond or hold_h >= 120 or stop_hit:
                            slip = slippage_ticks(tk, pos['lots'])
                            if stop_hit:
                                exit_p = pos['entry_p']*(1-stop_pct/100) if direction=='long' else pos['entry_p']*(1+stop_pct/100)
                            else:
                                exit_p = cur_p
                            exit_p = exit_p - slip*ms if direction=='long' else exit_p + slip*ms
                            avg_entry = pos['avg_entry']
                            if direction=='long':
                                pnl = (exit_p - avg_entry)/ms*sp*pos['lots'] - fee*2*pos['lots']
                            else:
                                pnl = (avg_entry - exit_p)/ms*sp*pos['lots'] - fee*2*pos['lots']
                            eq += pnl; n += 1; slips.append(slip)
                            if pnl>0: wins += 1
                            peak = max(peak, eq); mdd = max(mdd, (peak-eq)/peak*100)
                            pos = None
                    if pos is None:
                        in_cond = (dn <= -thr) if direction=='long' else (dn >= thr)
                        if in_cond:
                            idx = bisect.bisect_right(pts, ts) - 1
                            if idx < 0: continue
                            fill_p = bars[idx,4]
                            lots = max(1, int(eq*risk/go))  # ОТ ТЕКУЩЕГО eq (компаунд!)
                            lots = min(lots, max_lots)
                            max_lots_seen = max(max_lots_seen, lots)
                            slip = slippage_ticks(tk, lots)
                            entry_p = fill_p + slip*ms if direction=='long' else fill_p - slip*ms
                            pos = {'entry_ts':ts, 'entry_p':entry_p, 'avg_entry':entry_p, 'pyra_prices':[], 'lots':lots, 'base_price':entry_p}
                    elif pos is not None and len(pos['pyra_prices']) < pyr-1:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx >= 0:
                            if direction=='long':
                                hi_b = bars[idx,2]
                                if (hi_b-pos['base_price'])/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos['lots']; slip = slippage_ticks(tk, add_lots)
                                    pyra_px = hi_b + slip*ms
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']; new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px)/new_ct
                                    pos['lots'] = new_ct; pos['pyra_prices'].append(pyra_px)
                            else:
                                lo_b = bars[idx,3]
                                if (pos['base_price']-lo_b)/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos['lots']; slip = slippage_ticks(tk, add_lots)
                                    pyra_px = lo_b - slip*ms
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']; new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px)/new_ct
                                    pos['lots'] = new_ct; pos['pyra_prices'].append(pyra_px)
        year_eq[y] = eq
    return eq, mdd, n, wins/n*100 if n else 0, year_eq, max_lots_seen, slips

eq, mdd, n, wr, year_eq, max_lots, slips = run_stepwise()
print()
print('=== ПОШАГОВЫЙ КОМПАУНД (lots от текущего eq, slippage растёт) ===')
print(f'CAGR 4г: {((eq/200000)**0.25-1)*100:+.0f}%  MDD: {mdd:.1f}%  N={n}  WR={wr:.0f}%  max_lots={max_lots}')
prev = 200000
for y in [2023,2024,2025,2026]:
    e = year_eq[y]
    x = e/prev
    # средний eq в году для оценки slippage
    print(f'  {y}: {prev:>13,.0f} → {e:>15,.0f}₽ (x{x:5.1f})')
    prev = e
print(f'  Итог: {eq:,.0f}₽')
slips = np.array(slips)
print(f'  Slippage: медиана {np.median(slips):.0f}т, 90й перц {np.percentile(slips,90):.0f}т, макс {slips.max():.0f}т')
print(f'  Сделок с slip>5т: {(slips>5).sum()} ({(slips>5).mean()*100:.0f}%)')
