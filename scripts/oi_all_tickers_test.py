#!/usr/bin/env python3 -u
"""Полный тест OI-конфига на ВСЕХ тикерах (BR/NG/SV/RN/GD/GZ/MM).
Реальный компаунд, lots от текущего eq, лимит = 5% ISS VOLTODAY.
"""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc, numpy as np, bisect
from datetime import datetime, timezone
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def irk_day(ts): return int((ts - 7*3600)//86400)
specs = {
    'BR':(27606,0.01,7.706110,4.0),'NG':(11593,0.001,7.706110,4.0),
    'SV':(10971,0.01,7.706110,4.0),'RN':(13586,1.0,1.0,7.22),
    'GD':(60007,0.1,7.847560,44.28),'GZ':(3049,1.0,1.0,1.96),
    'MM':(4861,0.05,0.5,1.51),
}
# 5% ISS VOLTODAY (известно: BR 813K, NG 629K, SV 415K; остальные проверим по ISS)
DAILY_ISS = {'BR': 813052, 'NG': 628634, 'SV': 414552, 'RN': 200000, 'GD': 120000, 'GZ': 90000, 'MM': 150000}
LIMITS = {k: max(10, int(v*0.05)) for k,v in DAILY_ISS.items()}

def live_ok(ts):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    msk_h = (dt.hour + 3) % 24
    return msk_h >= 10 or msk_h < 2

DATA = {}
for y in [2023,2024,2025,2026]:
    END = '2026-08-09' if y == 2026 else f'{y}-12-31'
    for fut_tk in specs:
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

def run_one_ticker(years, fut_tk, thr=3, exit_thr=1.5, pyr=5, pyra_pct=0.3, stop_pct=1.5):
    """Прогон по ОДНОМУ тикеру (отдельный капитал 200K) — изоляция edge."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    for y in years:
        net_map, bars = DATA[(y,fut_tk)]
        go, ms, sp, fee = specs[fut_tk]
        max_lots = LIMITS[fut_tk]
        pts = bars[:,0]
        fts = sorted(ts for ts, dn in net_map.items() if abs(dn) >= min(thr, exit_thr) - 0.5 and live_ok(ts))
        for direction in ['long','short']:
            pos = None
            for ts in fts:
                dn = net_map[ts]
                if pos is not None:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx < 0: continue
                    cur_p = bars[idx,4]
                    lo = bars[idx,3]; hi = bars[idx,2]
                    exit_cond = (dn >= exit_thr) if direction=='long' else (dn <= -exit_thr)
                    hold_h = (ts - pos['entry_ts'])/3600
                    stop_hit = False
                    if stop_pct is not None:
                        if direction=='long' and lo <= pos['entry_p']*(1-stop_pct/100): stop_hit = True
                        if direction=='short' and hi >= pos['entry_p']*(1+stop_pct/100): stop_hit = True
                    if exit_cond or hold_h >= 120 or stop_hit:
                        if stop_hit:
                            exit_p = pos['entry_p']*(1-stop_pct/100) - ms if direction=='long' else pos['entry_p']*(1+stop_pct/100) + ms
                        else:
                            exit_p = cur_p - ms if direction=='long' else cur_p + ms
                        pnl = 0.0
                        for p_in in [pos['entry_p']] + pos['pyra_prices']:
                            if direction=='long': pnl += ((exit_p-p_in)/ms*sp - fee*2)*pos['lots']
                            else: pnl += ((p_in-exit_p)/ms*sp - fee*2)*pos['lots']
                        eq += pnl; n += 1
                        if pnl>0: wins += 1
                        peak = max(peak, eq)
                        mdd = max(mdd, (peak-eq)/peak*100)
                        pos = None
                if pos is None:
                    in_cond = (dn <= -thr) if direction=='long' else (dn >= thr)
                    if in_cond:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        fill_p = bars[idx,4] + ms if direction=='long' else bars[idx,4] - ms
                        lots = max(1, int(eq*0.10/go))
                        lots = min(lots, max_lots)
                        pos = {'entry_ts':ts,'entry_p':fill_p,'pyra_prices':[],'lots':lots}
                elif pos is not None and len(pos['pyra_prices']) < pyr-1:
                    idx = bisect.bisect_right(pts, ts) - 1
                    if idx >= 0:
                        if direction=='long':
                            hi_b = bars[idx,2]
                            if (hi_b-pos['entry_p'])/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                pos['pyra_prices'].append(hi_b+ms)
                        else:
                            lo_b = bars[idx,3]
                            if (pos['entry_p']-lo_b)/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                pos['pyra_prices'].append(lo_b-ms)
    roi = (eq/200000-1)*100
    wr = wins/n*100 if n else 0
    return roi, mdd, n, wr

print()
print('=== OI-конфиг (thr3 exit1.5 pyr5 stop1.5%) на каждом тикере отдельно, 2023-26 ===')
print('тикер | ROI      MDD     N    WR   Calmar')
for tk in ['BR','NG','SV','RN','GD','GZ','MM']:
    try:
        roi, mdd, n, wr = run_one_ticker([2023,2024,2025,2026], tk)
        print(f'{tk:5s} | {roi:+7.0f}%  {mdd:5.1f}%  {n:5d}  {wr:4.0f}%  {roi/max(mdd,0.1):6.0f}', flush=True)
    except Exception as e:
        print(f'{tk:5s} | ОШИБКА: {e}', flush=True)
