#!/usr/bin/env python3 -u
"""Бэктест OI, МАКСИМАЛЬНО приближенный к паперу (дом-исполнение).

Отличия от старых бэктестов (повторяют папер после фиксов 11.08):
1. slippage на вход/выход = функция размера лота из реальной глубины стакана
   (аппроксимация: lots(n) = a*n^b, обратная: n(lots) = (lots/a)^(1/b))
2. пирамидинг: пересчёт средней цены входа (как папер _close_pos)
3. порог пирамиды от pyra_base_price (первоначальный вход, не средняя)
4. стоп-лосс и все выходы с тем же slippage
5. риск per-ticker: BR=0.15, NG=0.10, SV=0.05 (как PG)
6. лимиты = средний дневной объём за 5 дней × LIQ_FRAC 0.05

Глубина стакана (медианные lots по уровням, из moex.dom 29.07-10.08):
  BRU6: lots ≈ 13 × n^1.28
  NGQ6: lots ≈ 88 × n^1.14
  SVU6: lots ≈ 18 × n^1.29
"""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc, numpy as np, bisect
from datetime import datetime, timezone
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

def irk_day(ts): return int((ts - 7*3600)//86400)
specs = {
    'BR':(27606,0.01,7.706110,4.0),'NG':(6093,0.001,7.706110,4.0),
    'SV':(10971,0.01,7.706110,4.0),
}
RISKS = {'BR': 0.15, 'NG': 0.10, 'SV': 0.05}  # как PG после per-ticker
# Глубина: (a, b) из lots = a*n^b → n(lots) = (lots/a)^(1/b) тиков
DEPTH = {'BR': (13, 1.28), 'NG': (88, 1.14), 'SV': (18, 1.29)}
# Средний дневной объём за 5 дней (лимит = 5%)
DAILY5 = {'BR': 609402, 'NG': 401991, 'SV': 349493}
LIMITS = {k: max(10, int(v*0.05)) for k,v in DAILY5.items()}

def slippage_ticks(tk, lots):
    """Сколько тиков slippage для lots лотов (из глубины стакана)."""
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

def run_portfolio(years=[2023,2024,2025,2026], thr=3, exit_thr=1.5, pyr=5, pyra_pct=0.3, stop_pct=1.5):
    """Портфель BR+NG+SV, общий eq, дом-исполнение как папер."""
    eq = 200000.0; peak = eq; mdd = 0.0
    n = 0; wins = 0
    slip_stats = []
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
                        cur_p = bars[idx,4]
                        lo = bars[idx,3]; hi = bars[idx,2]
                        exit_cond = (dn >= exit_thr) if direction=='long' else (dn <= -exit_thr)
                        hold_h = (ts - pos['entry_ts'])/3600
                        stop_hit = False
                        if stop_pct is not None:
                            if direction=='long' and lo <= pos['entry_p']*(1-stop_pct/100): stop_hit = True
                            if direction=='short' and hi >= pos['entry_p']*(1+stop_pct/100): stop_hit = True
                        if exit_cond or hold_h >= 120 or stop_hit:
                            # Выход с дом-slippage: сколько тиков нужно для contracts
                            slip = slippage_ticks(tk, pos['lots'])
                            if stop_hit:
                                exit_p = pos['entry_p']*(1-stop_pct/100) if direction=='long' else pos['entry_p']*(1+stop_pct/100)
                            else:
                                exit_p = cur_p
                            # slippage ухудшает выход: long продаём ниже, short покупаем выше
                            exit_p = exit_p - slip*ms if direction=='long' else exit_p + slip*ms
                            pnl = 0.0
                            # PnL по СРЕДНЕЙ цене (как папер после фикса)
                            avg_entry = pos['avg_entry']
                            if direction=='long':
                                pnl = (exit_p - avg_entry)/ms*sp*pos['lots'] - fee*2*pos['lots']
                            else:
                                pnl = (avg_entry - exit_p)/ms*sp*pos['lots'] - fee*2*pos['lots']
                            eq += pnl; n += 1
                            slip_stats.append(slip)
                            if pnl>0: wins += 1
                            peak = max(peak, eq)
                            mdd = max(mdd, (peak-eq)/peak*100)
                            pos = None
                    if pos is None:
                        in_cond = (dn <= -thr) if direction=='long' else (dn >= thr)
                        if in_cond:
                            idx = bisect.bisect_right(pts, ts) - 1
                            if idx < 0: continue
                            fill_p = bars[idx,4]
                            lots = max(1, int(eq*risk/go))
                            lots = min(lots, max_lots)
                            # Вход с дом-slippage
                            slip = slippage_ticks(tk, lots)
                            entry_p = fill_p + slip*ms if direction=='long' else fill_p - slip*ms
                            pos = {'entry_ts':ts, 'entry_p':entry_p, 'avg_entry':entry_p,
                                   'pyra_prices':[], 'lots':lots, 'base_price':entry_p}
                    elif pos is not None and len(pos['pyra_prices']) < pyr-1:
                        idx = bisect.bisect_right(pts, ts) - 1
                        if idx >= 0:
                            # Порог от base_price (первоначальный вход, как папер pyra_base_price)
                            if direction=='long':
                                hi_b = bars[idx,2]
                                if (hi_b-pos['base_price'])/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos['lots']  # base_contracts как в папере
                                    slip = slippage_ticks(tk, add_lots)
                                    pyra_px = hi_b + slip*ms  # покупаем по ask+slip
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']
                                    new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px) / new_ct
                                    pos['lots'] = new_ct
                                    pos['pyra_prices'].append(pyra_px)
                            else:
                                lo_b = bars[idx,3]
                                if (pos['base_price']-lo_b)/pos['base_price']*100 >= (len(pos['pyra_prices'])+1)*pyra_pct:
                                    add_lots = pos['lots']
                                    slip = slippage_ticks(tk, add_lots)
                                    pyra_px = lo_b - slip*ms
                                    old_ct = pos['lots']; old_avg = pos['avg_entry']
                                    new_ct = old_ct + add_lots
                                    pos['avg_entry'] = (old_ct*old_avg + add_lots*pyra_px) / new_ct
                                    pos['lots'] = new_ct
                                    pos['pyra_prices'].append(pyra_px)
    roi = (eq/200000-1)*100
    wr = wins/n*100 if n else 0
    avg_slip = np.mean(slip_stats) if slip_stats else 0
    return roi, mdd, n, wr, avg_slip

print()
print('=== OI ДОМ-БЭКТЕСТ (как папер): thr3 exit1.5 pyr5 stop1.5, risk per-ticker ===')
print('Год    | ROI       MDD     N    WR   ср.slip')
eq = 200000.0
for y in [2023, 2024, 2025, 2026]:
    roi, mdd, n, wr, slip = run_portfolio(years=[y])
    lab = f'{y}'
    print(f'{lab:5s} | {roi:+9.0f}%  {mdd:5.1f}%  {n:5d}  {wr:3.0f}%  {slip:.1f}т')
print()
roi, mdd, n, wr, slip = run_portfolio(years=[2023,2024,2025,2026])
print(f'ВСЕ | {roi:+9.0f}%  {mdd:5.1f}%  {n:5d}  {wr:3.0f}%  {slip:.1f}т')
print(f'CAGR 4г: {((eq*(1+roi/100)/200000)**0.25-1)*100:+.0f}%' if False else f'ROI 4г: {roi:+.0f}%')
