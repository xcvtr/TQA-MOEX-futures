#!/usr/bin/env python3 -u
"""Быстрый sweep: данные загружаются ОДИН раз в память, потом 450 прогонов."""
import sys, time
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc, numpy as np, bisect
from datetime import datetime, timezone
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def irk_day(ts): return int((ts - 7*3600)//86400)
specs = {'BR':(26903,0.01,7.706110,4.0),'NG':(6019,0.001,7.706110,4.0),'SV':(10509,0.01,7.706110,4.0)}
LIMITS = {'BR':100,'NG':100,'SV':80}

def live_ok(ts):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    msk_h = (dt.hour + 3) % 24
    return msk_h >= 10 or msk_h < 2

print("Загрузка данных в память...", flush=True)
t0 = time.time()
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
print(f"Данные загружены за {time.time()-t0:.0f}с", flush=True)

def run(risk=0.10, thr=3, exit_thr=2, pyr=3, pyra_pct=0.5, stop_pct=None):
    eq = 200000.0; peak_cash = eq; peak_mtm = eq
    cash_mdd = mtm_mdd = 0.0; n=0; wins=0; losses=0
    for y in [2023,2024,2025,2026]:
        for fut_tk in ['BR','NG','SV']:
            net_map, bars = DATA[(y,fut_tk)]
            go, ms, sp, fee = specs[fut_tk]
            max_lots = LIMITS[fut_tk]
            pts = bars[:,0]
            # Только ts, где |dn| >= min(thr, exit_thr) - 0.5 (иначе нет ни входа ни выхода)
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
                            if direction=='long' and lo <= pos['entry_p']*(1-stop_pct/100):
                                stop_hit = True
                            if direction=='short' and hi >= pos['entry_p']*(1+stop_pct/100):
                                stop_hit = True
                        if exit_cond or hold_h >= 120 or stop_hit:
                            if stop_hit:
                                exit_p = pos['entry_p']*(1-stop_pct/100) - ms if direction=='long' else pos['entry_p']*(1+stop_pct/100) + ms
                            else:
                                exit_p = cur_p - ms if direction=='long' else cur_p + ms
                            pnl = 0.0
                            for p_in in [pos['entry_p']] + pos['pyra_prices']:
                                if direction=='long': pnl += ((exit_p-p_in)/ms*sp - fee*2)*pos['lots']
                                else: pnl += ((p_in-exit_p)/ms*sp - fee*2)*pos['lots']
                            i0 = bisect.bisect_right(pts, pos['entry_ts']) - 1
                            i1 = bisect.bisect_right(pts, ts) - 1
                            # MTM: только min(lo) на окне сделки (векторизовано, без перебора баров)
                            if i1 >= i0 and i0 >= 0:
                                lo_min = bars[i0:i1+1, 3].min()
                                mtm_pnl = 0.0
                                for p_in in [pos['entry_p']] + pos['pyra_prices']:
                                    if direction=='long': mtm_pnl += ((lo_min-p_in)/ms*sp - fee*2)*pos['lots']
                                    else: mtm_pnl += ((p_in-lo_min)/ms*sp - fee*2)*pos['lots']
                                mtm_eq = eq + mtm_pnl
                                peak_mtm = max(peak_mtm, mtm_eq)
                                if peak_mtm > 0: mtm_mdd = max(mtm_mdd, (peak_mtm-mtm_eq)/peak_mtm*100)
                            eq += pnl; n += 1
                            if pnl>0: wins += 1
                            else: losses += 1
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
                                hi_b = bars[idx,2]
                                if (hi_b-pos['entry_p'])/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    pos['pyra_prices'].append(hi_b+ms)
                            else:
                                lo_b = bars[idx,3]
                                if (pos['entry_p']-lo_b)/pos['entry_p']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    pos['pyra_prices'].append(lo_b-ms)
    cagr = ((eq/200000)**(1/4)-1)*100 if eq > 0 else -100
    return cagr, cash_mdd, mtm_mdd, n, (wins/(wins+losses)*100 if wins+losses else 0)

# Sweep
print("=== SWEEP (MDD<=20% цель) ===", flush=True)
results = []
t0 = time.time()
for risk in [0.03, 0.05, 0.07, 0.10]:
    for thr in [3, 4, 5]:
        for exit_thr in [1.0, 1.5, 2.0, 3.0]:
            for pyr in [1, 2, 3]:
                for stop in [None, 1.0, 2.0, 3.0, 5.0]:
                    cagr, cd, md, n, wr = run(risk=risk, thr=thr, exit_thr=exit_thr, pyr=pyr, stop_pct=stop)
                    results.append((cagr, cd, md, n, wr, risk, thr, exit_thr, pyr, stop))
print(f"Sweep за {time.time()-t0:.0f}с", flush=True)

ok = [r for r in results if r[2] <= 20 and r[3] >= 200]
ok.sort(key=lambda r: -r[0])
print(f"\n--- Топ с MTM MDD<=20% и N>=200 ({len(ok)} из {len(results)}) ---", flush=True)
for cagr, cd, md, n, wr, risk, thr, exit_thr, pyr, stop in ok[:20]:
    stop_s = f"{stop}%" if stop else "нет"
    print(f"risk{risk:.0%} thr{thr} exit{exit_thr} pyr{pyr} stop{stop_s:>4}: CAGR {cagr:+7.0f}%  CashMDD {cd:5.1f}%  MTM {md:5.1f}%  N={n:5d}  WR={wr:.0f}%", flush=True)
if not ok:
    print("Нет MDD<=20%! По Calmar:", flush=True)
    results.sort(key=lambda r: r[0]/max(r[2],0.1))
    for cagr, cd, md, n, wr, risk, thr, exit_thr, pyr, stop in results[:12]:
        stop_s = f"{stop}%" if stop else "нет"
        print(f"risk{risk:.0%} thr{thr} exit{exit_thr} pyr{pyr} stop{stop_s:>4}: CAGR {cagr:+7.0f}%  CashMDD {cd:5.1f}%  MTM {md:5.1f}%  N={n:5d}  WR={wr:.0f}%", flush=True)
