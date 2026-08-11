#!/usr/bin/env python3 -u
"""Подгонка risk под MTM DD<=20% с реалистичными подвохами:
1. стоп-пробой: стоп исполняется на 1% хуже (средний пробой из данных)
2. пирамидинг по ask/bid+slip (не hi/lo)
3. MDD по MTM (lo/hi внутри позиции)
4. айсберг slippage 1-2т
"""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc, numpy as np, bisect
from datetime import datetime, timezone
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def irk_day(ts): return int((ts - 7*3600)//86400)
specs = {
    'BR':(27606,0.01,7.706110,9.98),'NG':(6093,0.001,7.706110,3.56),
    'SV':(10971,0.01,7.706110,7.66),
}
STOP_GAP = {'BR': 1.1, 'NG': 1.07, 'SV': 0.8}  # средний пробой стопа в %
DAILY5 = {'BR': 609402, 'NG': 401991, 'SV': 349493}
LIMITS = {k: max(10, int(v*0.05)) for k,v in DAILY5.items()}

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

def run_risks(risks, years=[2023,2024,2025,2026], thr=3, exit_thr=1.5, pyr=5, pyra_pct=0.3, stop_pct=1.5):
    eq = 200000.0; peak_cash = eq; cash_mdd = 0.0
    peak_mtm = eq; mtm_mdd = 0.0
    n = 0; wins = 0
    for y in years:
        for tk in ['BR','NG','SV']:
            risk = risks[tk]
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
                        # MTM по lo/hi внутри позиции
                        mtm_px = lo if direction=='long' else hi
                        avg_entry = pos['avg_entry']
                        if direction=='long':
                            mtm_pnl = (mtm_px - avg_entry)/ms*sp*pos['lots'] - fee*2*pos['lots']
                        else:
                            mtm_pnl = (avg_entry - mtm_px)/ms*sp*pos['lots'] - fee*2*pos['lots']
                        mtm_eq = eq + mtm_pnl
                        peak_mtm = max(peak_mtm, mtm_eq)
                        if peak_mtm > 0: mtm_mdd = max(mtm_mdd, (peak_mtm-mtm_eq)/peak_mtm*100)
                        # выход
                        exit_cond = (dn >= exit_thr) if direction=='long' else (dn <= -exit_thr)
                        hold_h = (ts - pos['entry_ts'])/3600
                        stop_hit = False
                        if stop_pct is not None:
                            if direction=='long' and lo <= pos['entry_p']*(1-stop_pct/100): stop_hit = True
                            if direction=='short' and hi >= pos['entry_p']*(1+stop_pct/100): stop_hit = True
                        if exit_cond or hold_h >= 120 or stop_hit:
                            slip = 1 if tk=='NG' else 2
                            if stop_hit:
                                # стоп-пробой: исполняемся на stop_pct + средний пробой
                                gap = STOP_GAP[tk]
                                exit_p = pos['entry_p']*(1-(stop_pct+gap)/100) if direction=='long' else pos['entry_p']*(1+(stop_pct+gap)/100)
                            else:
                                exit_p = cur_p
                            exit_p = exit_p - slip*ms if direction=='long' else exit_p + slip*ms
                            if direction=='long':
                                pnl = (exit_p - avg_entry)/ms*sp*pos['lots'] - fee*2*pos['lots']
                            else:
                                pnl = (avg_entry - exit_p)/ms*sp*pos['lots'] - fee*2*pos['lots']
                            eq += pnl; n += 1
                            if pnl>0: wins += 1
                            peak_cash = max(peak_cash, eq)
                            cash_mdd = max(cash_mdd, (peak_cash-eq)/peak_cash*100)
                            pos = None
                    if pos is None:
                        in_cond = (dn <= -thr) if direction=='long' else (dn >= thr)
                        if in_cond:
                            idx = bisect.bisect_right(pts, ts) - 1
                            if idx < 0: continue
                            fill_p = bars[idx,4]
                            lots = max(1, int(eq*risk/go))
                            lots = min(lots, max_lots)
                            slip = 1 if tk=='NG' else 2
                            entry_p = fill_p + slip*ms if direction=='long' else fill_p - slip*ms
                            pos = {'entry_ts':ts, 'entry_p':entry_p, 'avg_entry':entry_p, 'pyra_prices':[], 'lots':lots, 'base_price':entry_p}
                    elif pos is not None and len(pos['pyra_prices']) < pyr-1:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx >= 0:
                            if direction=='long':
                                hi_b = bars[idx,2]
                                if (hi_b-pos['base_price'])/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos['lots']; slip = 1 if tk=='NG' else 2
                                    pyra_px = hi_b + slip*ms  # ask = hi + slip (консервативно)
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']; new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px)/new_ct
                                    pos['lots'] = new_ct; pos['pyra_prices'].append(pyra_px)
                            else:
                                lo_b = bars[idx,3]
                                if (pos['base_price']-lo_b)/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos['lots']; slip = 1 if tk=='NG' else 2
                                    pyra_px = lo_b - slip*ms  # bid = lo - slip
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']; new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px)/new_ct
                                    pos['lots'] = new_ct; pos['pyra_prices'].append(pyra_px)
    return eq, cash_mdd, mtm_mdd, n, wins/n*100 if n else 0

print()
print('=== SWEEP: риск → MTM DD (реалистичная модель) ===')
print('риски BR/NG/SV       | CAGR     CashMDD  MTM_DD   N    WR')
configs = [
    ('15/10/5 (текущ)', {'BR':0.15,'NG':0.10,'SV':0.05}),
    ('10/7/3',          {'BR':0.10,'NG':0.07,'SV':0.03}),
    ('8/5/2',           {'BR':0.08,'NG':0.05,'SV':0.02}),
    ('6/4/2',           {'BR':0.06,'NG':0.04,'SV':0.02}),
    ('5/3/2',           {'BR':0.05,'NG':0.03,'SV':0.02}),
    ('4/3/2',           {'BR':0.04,'NG':0.03,'SV':0.02}),
    ('8/6/3',           {'BR':0.08,'NG':0.06,'SV':0.03}),
    ('7/5/2',           {'BR':0.07,'NG':0.05,'SV':0.02}),
    ('6/5/3',           {'BR':0.06,'NG':0.05,'SV':0.03}),
]
for name, risks in configs:
    eq, cash_mdd, mtm_mdd, n, wr = run_risks(risks)
    cagr = ((eq/200000)**0.25-1)*100 if eq > 0 else -100
    mark = ' ✅' if mtm_mdd <= 20 else ''
    print(f'{name:16s} | {cagr:+7.0f}%  {cash_mdd:5.1f}%  {mtm_mdd:5.1f}%  {n:5d}  {wr:3.0f}%{mark}')
