#!/usr/bin/env python3 -u
"""ПРАВИЛЬНЫЙ аудит: компаунд + честный пирамидинг + MTM в одном прогоне.

Генерация сделок и симуляция СОВМЕЩЕНЫ (eq обновляется на лету).
Лоты от текущего eq (компаунд), лимиты, маржа, MTM.
"""
import sys, bisect
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc, psycopg2
from datetime import datetime, timezone

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
DAY_SEC = 86400
ALL = {'BR': 'BR', 'NG': 'NG', 'SV': 'SILV', 'RI': 'RTSI', 'TT': 'TATN'}
TICKER_LIMITS = {'BR': 100, 'NG': 100, 'SV': 80, 'RN': 80, 'RI': 50, 'TT': 30}
MAX_MARGIN = 0.80

conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='postgres', connect_timeout=5)
cur = conn.cursor()
cur.execute("SELECT ticker, go, min_step, step_price, fee_entry FROM futures.ticker_specs")
specs = {}
for t, go, ms, sp, fee in cur.fetchall(): specs[t] = (float(go), float(ms), float(sp), float(fee))
conn.close()

def irk_day(ts): return int((ts - 7*3600) // DAY_SEC)

def load_year(y, fut_tk, mt_tk):
    START, END = f'{y}-01-01', f'{y}-12-31'
    if y == 2026: END = '2026-08-09'
    r = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), buy_fiz, sell_fiz, buy_yur, sell_yur "
                 f"FROM moex.futoi WHERE ticker='{fut_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    day_start = {}; net_map = {}
    for ts, fb, fs, yb, ys in r:
        d = irk_day(ts)
        if d not in day_start: day_start[d] = int(fb) - int(fs)
        total = int(fb)+int(fs)+int(yb)+int(ys)
        if total <= 0: continue
        net_map[ts] = (int(fb)-int(fs)-day_start[d]) / total * 100
    r2 = ch.query(f"SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc "
                  f"FROM moex.mt5_continuous WHERE ticker='{mt_tk}' AND bt>='{START}' AND bt<='{END} 23:59:59'").result_rows
    arr = np.array([(ts,o,h,l,c) for ts,o,h,l,c in r2 if c and c>0], dtype=np.float64)
    if arr.size == 0: return None
    o = np.argsort(arr[:,0])
    return net_map, arr[o]

def run(years, risk=0.10, thr=3, exit_thr=2, pyr=3, pyra_pct=0.5):
    """Совмещённый прогон: eq обновляется на лету при каждой сделке."""
    eq = 200000.0; peak_cash = eq; peak_mtm = eq
    cash_mdd = mtm_mdd = 0.0; n=0; wins=0
    for y in years:
        for fut_tk, mt_tk in ALL.items():
            if fut_tk not in specs: continue
            d = load_year(y, fut_tk, mt_tk)
            if d is None: continue
            net_map, bars = d
            go, ms, sp, fee = specs[fut_tk]
            max_lots = TICKER_LIMITS.get(fut_tk, 50)
            pts = bars[:,0]; fts = sorted(net_map.keys())
            for direction in ['long','short']:
                pos = None
                for ts in fts:
                    dn = net_map[ts]
                    if pos is not None:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        cur_p = bars[idx,4]
                        exit_cond = (dn >= exit_thr) if direction=='long' else (dn <= -exit_thr)
                        hold_h = (ts - pos['entry_ts'])/3600
                        if exit_cond or hold_h >= 120:
                            exit_p = cur_p - ms if direction=='long' else cur_p + ms
                            pnl = 0.0
                            for p_in in [pos['entry_p']] + pos['pyra_prices']:
                                if direction=='long': pnl += ((exit_p-p_in)/ms*sp - fee*2)*pos['lots']
                                else: pnl += ((p_in-exit_p)/ms*sp - fee*2)*pos['lots']
                            # MTM на барах внутри сделки
                            i0 = bisect.bisect_right(pts, pos['entry_ts']) - 1
                            i1 = bisect.bisect_right(pts, ts) - 1
                            for bi in range(max(0,i0), min(i1+1, len(bars))):
                                lo = bars[bi,3]
                                mtm_pnl = 0.0
                                for p_in in [pos['entry_p']] + pos['pyra_prices']:
                                    if direction=='long': mtm_pnl += ((lo-p_in)/ms*sp - fee*2)*pos['lots']
                                    else: mtm_pnl += ((p_in-lo)/ms*sp - fee*2)*pos['lots']
                                mtm_eq = eq + mtm_pnl
                                peak_mtm = max(peak_mtm, mtm_eq)
                                if peak_mtm > 0:
                                    mtm_mdd = max(mtm_mdd, (peak_mtm-mtm_eq)/peak_mtm*100)
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
                            fill_p = bars[idx,4] + ms if direction=='long' else bars[idx,4] - ms
                            lots = max(1, int(eq*risk/go))
                            lots = min(lots, max_lots)
                            pos = {'entry_ts':ts,'entry_p':fill_p,'pyra_prices':[],'lots':lots}
                    elif pos is not None and len(pos['pyra_prices']) < pyr-1:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx >= 0:
                            if direction=='long':
                                hi = bars[idx,2]
                                if (hi-pos['entry_p'])/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    pos['pyra_prices'].append(hi+ms)
                            else:
                                lo = bars[idx,3]
                                if (pos['entry_p']-lo)/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    pos['pyra_prices'].append(lo-ms)
    return eq, cash_mdd, mtm_mdd, n, wins/n*100

print("ПРАВИЛЬНЫЙ аудит (компаунд + честный пирамидинг + MTM):")
for yrs, label in [([2023,2024,2025,2026],'2023-26'), ([2022],'2022 OOS')]:
    for risk, thr, ex, pyr in [(0.05,3,2,3),(0.10,3,2,3),(0.10,4,2,3),(0.10,3,3,3),(0.20,3,2,3),(0.10,3,2,1)]:
        eq, cd, md, n, wr = run(yrs, risk=risk, thr=thr, exit_thr=ex, pyr=pyr)
        cagr = ((eq/200000)**(1/len(yrs))-1)*100 if len(yrs)==4 else (eq/200000-1)*100
        print(f"{label} risk{risk:.0%} thr{thr} ex{ex} pyr{pyr}: "
              f"{'CAGR' if len(yrs)==4 else 'ROI'} {cagr:+.1f}%  CashMDD {cd:.1f}%  MTM {md:.1f}%  N={n}  WR={wr:.1f}%")
