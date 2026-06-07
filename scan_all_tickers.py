#!/usr/bin/env python3
"""Scan ALL symbols from moex_prices_5m with the realistic TP/SL model."""
import sys, os, json, math
import psycopg2
import numpy as np
from datetime import datetime, timedelta

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='***')
H4_WINDOW = 20
TARGET_BARS = 2
ENTRY_SLIPPAGE = 0.001
TP_PCT = 0.004
SL_PCT = 0.008
TRAIL_ACTIVATE = 0.005

GO_DATA = {
    'SS': {'go_rub': 205, 'lev': 2.0}, 'W4': {'go_rub': 1758, 'lev': 9.2},
    'VB': {'go_rub': 1363, 'lev': 5.7}, 'GD': {'go_rub': 26922, 'lev': 12.1},
    'SR': {'go_rub': 5719, 'lev': 5.8}, 'SV': {'go_rub': 11487, 'lev': 4.8},
    'GZ': {'go_rub': 2065, 'lev': 5.7}, 'PD': {'go_rub': 22173, 'lev': 4.6},
    'LK': {'go_rub': 10218, 'lev': 4.9}, 'GL': {'go_rub': 1220, 'lev': 8.7},
    'RI': {'go_rub': 24668, 'lev': 6.6}, 'NG': {'go_rub': 6565, 'lev': 3.5},
    'CC': {'go_rub': 473, 'lev': 6.4}, 'CH': {'go_rub': 538, 'lev': 7.8},
    'IB': {'go_rub': 803, 'lev': 3.5}, 'NM': {'go_rub': 1405, 'lev': 5.8},
    'SN': {'go_rub': 8180, 'lev': 4.9}, 'BR': {'go_rub': 1702, 'lev': 3.8},
    'NR': {'go_rub': 1536, 'lev': 4.9}, 'HY': {'go_rub': 804, 'lev': 4.9},
    'OJ': {'go_rub': 2019, 'lev': 5.9}, 'SE': {'go_rub': 625, 'lev': 1.4},
    'DX': {'go_rub': 0, 'lev': 5.0}, 'BM': {'go_rub': 0, 'lev': 5.0},
}

NAMES = {
    'SS':'Sugar','W4':'Wheat','VB':'VTB','GD':'Gold','SR':'Sberbank','SV':'Silver',
    'GZ':'Gold Z','PD':'Palladium','LK':'Lukoil','GL':'Gold L','RI':'RTS Index',
    'NG':'Nat Gas','CC':'Cocoa C','CH':'Cocoa','IB':'I-Bonds','NM':'Norilsk',
    'SN':'Tin','BR':'Brent','NR':'Nat Rubber','HY':'Hryvnia','OJ':'Orange Juice',
    'SE':'Soybean','DX':'Dollar Index','BM':'Butter',
    'MC':'Micex','ME':'Micex E','NA':'Nano','MX':'Micex X','AF':'Alfa','AL':'Alum',
    'AU':'Aurum','CE':'Cedar','CNYRUBF':'CNY/RUB','ED':'Edel','Eu':'Euro',
    'EURRUBF':'EUR/RUB','FF':'Fifty','GAZPF':'Gazprom','GK':'Gk','HS':'Hogs',
    'IMOEXF':'IMOEX','KC':'KC','MG':'MG','MM':'MM','PT':'Plat','RM':'RM',
    'RN':'RNG','SBERF':'Sber F','SF':'SF','Si':'SILVER','SP':'SP','TN':'TN',
    'TT':'TT','UC':'UC','USDRUBF':'USD/RUB','VI':'VI','X5':'X5','YD':'YD',
    'GLDRUBF':'GLD/RUB'
}

conn = psycopg2.connect(**DB)
cur = conn.cursor()

cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m WHERE volume > 0 AND time >= '2024-01-01' ORDER BY symbol")
all_symbols = [r[0] for r in cur.fetchall()]

results = []
for sym in all_symbols:
    go_info = GO_DATA.get(sym, {'lev': 5.0})
    lev = go_info.get('lev', 5.0)
    
    cur.execute("SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol = %s AND time >= '2024-01-01' AND volume > 0 ORDER BY time", (sym,))
    rows = cur.fetchall()
    if len(rows) < 100:
        continue
    
    h4 = {}
    for t, o, h, l, c, v in rows:
        h4_key = t.replace(minute=0, second=0, microsecond=0) - timedelta(hours=t.hour % 4)
        if h4_key not in h4:
            h4[h4_key] = [t, o, h, l, c, v]
        else:
            prev = h4[h4_key]
            h4[h4_key] = [prev[0], prev[1], max(prev[2], h), min(prev[3], l), c, prev[5] + v]
    h4_bars = sorted(h4.values(), key=lambda x: x[0])
    
    if len(h4_bars) < H4_WINDOW + TARGET_BARS + 10:
        continue
    
    data = []
    for i, (t, o, h, l, c, v) in enumerate(h4_bars):
        d = {'time': t, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
             'range_pct': (h - l) / l * 100 if l else 0}
        if i >= H4_WINDOW:
            window = h4_bars[i - H4_WINDOW:i]
            vols = [w[5] for w in window]
            med_vol = np.median(vols) if vols else 1
            d['vol_ratio'] = v / max(med_vol, 1)
            ranges = [(w[2] - w[3]) / w[3] * 100 for w in window if w[3] > 0]
            d['avg_range_pct'] = np.mean(ranges) if ranges else 0
            d['close_pos'] = (c - l) / (h - l) if h != l else 0.5
        else:
            d['vol_ratio'] = 0
            d['avg_range_pct'] = 0
            d['close_pos'] = 0.5
        data.append(d)
    
    sigs = []
    for i, d in enumerate(data):
        if d['vol_ratio'] <= 2 or d['range_pct'] <= d.get('avg_range_pct', 0):
            continue
        is_red = d['close'] < d['open']
        is_green = d['close'] > d['open']
        is_bear = is_red and d['close_pos'] <= 0.35
        is_bull = is_green and d['close_pos'] >= 0.65
        if not is_bear and not is_bull:
            continue
        if i + 1 + TARGET_BARS >= len(data):
            continue
        
        entry_bar = data[i+1]
        entry = entry_bar['open'] * (1 + ENTRY_SLIPPAGE)
        hold_bars = [data[i+1+k] for k in range(TARGET_BARS)]
        
        if is_bear:
            tp = entry * (1 + TP_PCT)
            sl = entry * (1 - SL_PCT)
            trail_be = entry * 1.001
            trail_sl = sl
            trailed = False
            real_exit = None
            real_reason = 'timeout'
            for bar in hold_bars:
                if bar['high'] >= tp:
                    real_exit = tp; real_reason = 'tp'; break
                if bar['low'] <= trail_sl:
                    real_exit = trail_sl; real_reason = 'sl'; break
                if not trailed and bar['high'] >= entry * (1 + TRAIL_ACTIVATE):
                    trail_sl = trail_be; trailed = True
            if real_exit is None:
                real_exit = hold_bars[-1]['close']; real_reason = 'expiry'
            real_ret = (real_exit - entry) / entry * 100
        else:
            tp = entry * (1 - TP_PCT)
            sl = entry * (1 + SL_PCT)
            trail_be = entry * 0.999
            trail_sl = sl
            trailed = False
            real_exit = None
            real_reason = 'timeout'
            for bar in hold_bars:
                if bar['low'] <= tp:
                    real_exit = tp; real_reason = 'tp'; break
                if bar['high'] >= trail_sl:
                    real_exit = trail_sl; real_reason = 'sl'; break
                if not trailed and bar['low'] <= entry * (1 - TRAIL_ACTIVATE):
                    trail_sl = trail_be; trailed = True
            if real_exit is None:
                real_exit = hold_bars[-1]['close']; real_reason = 'expiry'
            real_ret = (entry - real_exit) / entry * 100
        
        real_win = real_ret > 0
        sigs.append({'real_ret': real_ret, 'real_win': real_win, 'real_reason': real_reason})
    
    if len(sigs) < 10:
        continue
    
    n = len(sigs)
    real_ret_list = [s['real_ret'] for s in sigs]
    real_wins = sum(1 for s in sigs if s['real_win'])
    real_wr = real_wins / n * 100
    real_total = sum(real_ret_list)
    real_avg = np.mean(real_ret_list)
    real_gp = sum(p for p in real_ret_list if p > 0)
    real_gl = abs(sum(p for p in real_ret_list if p < 0))
    real_pf = real_gp / max(real_gl, 0.001)
    real_cum = np.cumsum(real_ret_list)
    real_peak = np.maximum.accumulate(real_cum)
    real_dd = real_cum - real_peak
    real_max_dd = min(real_dd) if len(real_dd) > 0 else 0
    
    go_total = sum(r * lev for r in real_ret_list)
    go_cum = np.cumsum([r * lev for r in real_ret_list])
    go_peak = np.maximum.accumulate(go_cum)
    go_dd = go_cum - go_peak
    go_max_dd = min(go_dd) if len(go_dd) > 0 else 0
    
    tp_cnt = sum(1 for s in sigs if s['real_reason'] == 'tp')
    sl_cnt = sum(1 for s in sigs if s['real_reason'] == 'sl')
    exp_cnt = sum(1 for s in sigs if s['real_reason'] == 'expiry')
    
    score = real_wr * real_pf * (1 + max(go_total, 0) / 100) / max(abs(go_max_dd) / 50, 0.5)
    
    results.append({
        'sym': sym,
        'name': NAMES.get(sym, sym),
        'signals': n,
        'real_wr': round(real_wr, 1),
        'real_total_pnl': round(real_total, 2),
        'go_total_pnl': round(go_total, 2),
        'real_pf': round(real_pf, 2),
        'real_max_dd': round(real_max_dd, 2),
        'go_max_dd': round(go_max_dd, 2),
        'real_avg_ret': round(real_avg, 3),
        'lev': lev,
        'tp_cnt': tp_cnt,
        'sl_cnt': sl_cnt,
        'exp_cnt': exp_cnt,
        'score': round(score, 1),
        'first_bar': str(h4_bars[0][0].date()),
        'last_bar': str(h4_bars[-1][0].date()),
    })
    print(f"{sym:>10} {n:>5} sig  WR {real_wr:5.1f}%  PF {real_pf:.2f}  GO {go_total:+7.1f}%  DD {go_max_dd:+7.0f}%  lev {lev:.1f}x  score {score:.0f}", flush=True)

conn.close()

results.sort(key=lambda x: x['score'], reverse=True)

print()
print("=" * 120)
hdr = f"{'#':>3} {'Sym':>10} {'Name':>14} {'Sig':>5} {'WR%':>5} {'PF':>5} {'GOΣ%':>7} {'GODD%':>7} {'NotΣ%':>7} {'Lev':>4} {'Score':>7} {'Period'}"
print(hdr)
print("-" * 120)
for i, r in enumerate(results[:40]):
    print(f"{i+1:>3} {r['sym']:>10} {r['name']:>14} {r['signals']:>5} {r['real_wr']:>5.1f} {r['real_pf']:>5.2f} {r['go_total_pnl']:>+7.1f} {r['go_max_dd']:>+7.0f} {r['real_total_pnl']:>+7.2f} {r['lev']:>4.1f}x {r['score']:>7.0f} {r['first_bar'][:7]}..{r['last_bar'][:7]}")

current = ['CH','W4','OJ','DX','BM','BR','NR','SV','SS','IB','NG','CC','SN','GZ','VB','PD','HY','SE','LK','GD','RI','GL','SR','NM']
curr_set = set(current)
top30_syms = set(r['sym'] for r in results[:30])
fallen = curr_set - top30_syms
newcomers = top30_syms - curr_set
print(f"\nТекущие чемпионы вне топ-30: {sorted(fallen) if fallen else '(все в топе)'}")
print(f"Новые в топ-30: {sorted(newcomers) if newcomers else '(нет)'}")
print(f"Всего обработано: {len(results)} тикеров с >= 10 сигналами")
