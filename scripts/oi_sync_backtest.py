#!/usr/bin/env python3 -u
"""СИНХРОНИЗИРОВАННЫЙ тестер OI — 1:1 с live-логикой paper_trader.py.

Логика = live (paper_trader.py):
- Вход:   |day_net| >= thr (3) → contrarian long при <= -thr, short при >= thr
- Выход:  day_net >= exit_thr (1.5) для long (обратное условие) или hold_limit (72ч) или стоп
- Стоп:   lo <= avg_entry*(1-stop_pct) — ОТ СРЕДНЕГО ВХОДА (как live p['entry_price']),
          exit по lo бара БЕЗ gap (live не добавляет стоп-пробой)
- Пирамид: pyr3 (2 добавки), +0.3% от base, add_lots = base_lots (как live base_contracts)
- Slippage: 1 тик (live dom_slip=1 для малых лотов)
- Комиссии: fee из specs (BR 9.98, NG 3.56, SV 7.66)
- Риски:    BR 15%, NG 10%, SV 5% от min(eq, cap=2M)
- Компаунд: lots растут с eq (кап 2M ограничивает)
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
DAILY5 = {'BR': 609402, 'NG': 401991, 'SV': 349493}
LIMITS = {k: max(10, int(v*0.05)) for k,v in DAILY5.items()}

def live_ok(ts):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    msk_h = (dt.hour + 3) % 24
    return msk_h >= 10 or msk_h < 2

DATA = {}
for y in [2023,2024,2025,2026]:
    END = '2026-08-13' if y == 2026 else f'{y}-12-31'
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

def run(risks=None, thr=3.0, exit_thr=1.5, pyr=3, pyra_pct=0.3, stop_pct=1.5,
        years=[2023,2024,2025,2026], eq_cap=2_000_000, hold_limit=72):
    """Синхронизированная модель: параметры как в PG + live-логика папера."""
    if risks is None:
        risks = {'BR': 0.15, 'NG': 0.10, 'SV': 0.05}  # как в PG
    eq = 200000.0; peak_cash = eq; cash_mdd = 0.0
    peak_mtm = eq; mtm_mdd = 0.0; n = 0; wins = 0
    max_lots = 0
    for y in years:
        for tk in ['BR','NG','SV']:
            risk = risks[tk]
            net_map, bars = DATA[(y,tk)]
            go, ms, sp, fee = specs[tk]
            max_lots_tk = LIMITS[tk]
            pts = bars[:,0]
            fts = sorted(ts for ts, dn in net_map.items() if abs(dn) >= 1.0 and live_ok(ts))
            for direction in ['long','short']:
                pos = None
                for ts in fts:
                    dn = net_map[ts]
                    if pos is not None:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx < 0: continue
                        cur_p = bars[idx,4]; lo = bars[idx,3]; hi = bars[idx,2]
                        # MTM по CLOSE (как live calc_mtm_equity)
                        mtm_px = cur_p
                        avg_entry = pos['avg_entry']
                        if direction=='long':
                            mtm_pnl = (mtm_px - avg_entry)/ms*sp*pos['lots'] - fee*2*pos['lots']
                        else:
                            mtm_pnl = (avg_entry - mtm_px)/ms*sp*pos['lots'] - fee*2*pos['lots']
                        mtm_eq = eq + mtm_pnl
                        peak_mtm = max(peak_mtm, mtm_eq)
                        if peak_mtm > 0: mtm_mdd = max(mtm_mdd, (peak_mtm-mtm_eq)/peak_mtm*100)
                        # Выход: ОИ (обратное условие) / hold / стоп — КАК LIVE
                        exit_cond = (dn >= exit_thr) if direction=='long' else (dn <= -exit_thr)
                        hold_h = (ts - pos['entry_ts'])/3600
                        stop_hit = False
                        # LIVE: стоп от BASE (pyra_base_price = первая цена), НЕ от avg
                        base_px = pos['base_price']
                        if direction=='long' and lo <= base_px*(1-stop_pct/100): stop_hit = True
                        if direction=='short' and hi >= base_px*(1+stop_pct/100): stop_hit = True
                        if exit_cond or hold_h >= hold_limit or stop_hit:
                            slip = 1  # live dom_slip=1
                            if stop_hit:
                                # LIVE: exit_price = lo (БЕЗ gap — live не добавляет пробой)
                                exit_p = lo if direction=='long' else hi
                            else:
                                exit_p = cur_p
                            # slippage на выходе: 1 тик (live: exit_long по стакану ~1 тик)
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
                            # LIVE: sizing от min(eq, cap), risk из PG
                            sizing_eq = min(eq, eq_cap) if eq_cap else eq
                            lots = max(1, int(sizing_eq*risk/go))
                            lots = min(lots, max_lots_tk)
                            max_lots = max(max_lots, lots)
                            slip = 1  # live dom_slip=1
                            entry_p = fill_p + slip*ms if direction=='long' else fill_p - slip*ms
                            pos = {'entry_ts':ts, 'entry_p':entry_p, 'avg_entry':entry_p,
                                   'pyra_prices':[], 'lots':lots, 'base_lots':lots, 'base_price':entry_p}
                    elif pos is not None and len(pos['pyra_prices']) < pyr-1:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx >= 0:
                            # Пирамидинг: +0.3% от base, add = base_lots (как live base_contracts)
                            if direction=='long':
                                hi_b = bars[idx,2]
                                if (hi_b-pos['base_price'])/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos.get('base_lots', pos['lots'])
                                    pyra_px = hi_b + 1*ms  # slip 1 тик
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']; new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px)/new_ct
                                    pos['lots'] = new_ct; pos['pyra_prices'].append(pyra_px)
                            else:
                                lo_b = bars[idx,3]
                                if (pos['base_price']-lo_b)/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos.get('base_lots', pos['lots'])
                                    pyra_px = lo_b - 1*ms
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']; new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px)/new_ct
                                    pos['lots'] = new_ct; pos['pyra_prices'].append(pyra_px)
    return eq, cash_mdd, mtm_mdd, n, wins/n*100 if n else 0, max_lots

if __name__ == '__main__':
    print()
    print('=== СИНХРОНИЗИРОВАННЫЙ ТЕСТЕР (логика = live paper_trader.py) ===')
    print('Параметры как в PG: thr=3, exit_thr=1.5, pyr3, hold 72ч, cap 2M, риски 15/10/5')
    print()
    # 1. По годам (отдельный старт 200K каждый год — сравнение)
    print('--- По годам (старт 200K) ---')
    print('Год  | ROI        MTM_DD   N    WR')
    for y in [2023,2024,2025,2026]:
        eq, cash_mdd, mtm_mdd, n, wr, ml = run(years=[y])
        roi = (eq/200000-1)*100
        print(f'{y} | {roi:+8.0f}%  {mtm_mdd:5.1f}%  {n:4d}  {wr:3.0f}%')
    # 2. Полный компаунд 2023-26
    print()
    print('--- Полный компаунд 2023-26 ---')
    eq, cash_mdd, mtm_mdd, n, wr, ml = run(years=[2023,2024,2025,2026])
    cagr = ((eq/200000)**0.25-1)*100
    print(f'CAGR {cagr:+6.0f}% | CashDD {cash_mdd:.1f}% | MTM DD {mtm_mdd:.1f}% | N={n} | WR {wr:.0f}% | max_lots={ml} | eq={eq/1e6:.1f}M')
