#!/usr/bin/env python3
"""Аудит кандидатов: look-ahead, B&H, вневыборка 2023 (если есть)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
COMM = 4
HOLD = 5

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

CS_MAP = {'GL': 10, 'NA': 1, 'PT': 10, 'GD': 10, 'RL': 1, 'W4': 1, 'MM': 1}

# Паттерны
PF = {
    'vol_up_oi_up_yb_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    'vol_up_yb_up_fiz_down': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'vol_up_oi_down': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'vol_up_yb_down_fiz_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'fiz_extreme_vol_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

# Кандидаты из предыдущего прогона
CANDIDATES = [
    ('GL', 'vol_up_oi_up_yb_up', 10),
    ('NA', 'vol_up_oi_up_yb_up', 1),
    ('PT', 'vol_up_yb_up_fiz_down', 10),
    ('GD', 'vol_up_yb_up_fiz_down', 10),
    ('GD', 'vol_up_oi_down', 10),
    ('RL', 'vol_up_yb_down_fiz_up', 1),
    ('W4', 'vol_up_yb_down_fiz_up', 1),
    ('PT', 'fiz_extreme_vol_up', 10),
]

def get_data(ticker, date_from='2024-01-01', date_to='2026-05-01', label=''):
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.time <= %(e)s
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker, 's': date_from, 'e': date_to}).result_rows
    
    if len(rows) < 30:
        return None
    
    dates = [str(r[0]) for r in rows]
    opn = np.array([r[1] for r in rows], dtype=float)
    high = np.array([r[2] for r in rows], dtype=float)
    low = np.array([r[3] for r in rows], dtype=float)
    close = np.array([r[4] for r in rows], dtype=float)
    vol = np.array([r[5] for r in rows], dtype=float)
    yb = np.array([r[6] for r in rows], dtype=float)
    fb = np.array([r[8] for r in rows], dtype=float)
    fs = np.array([r[9] for r in rows], dtype=float)
    toi = np.array([r[10] for r in rows], dtype=float)
    toi = np.where(toi <= 0, 1, toi)
    
    v_m = np.mean(vol) + 1
    yb_m = np.mean(yb) + 1
    toi_m = np.mean(toi) + 1
    
    dv = np.diff(vol) / v_m
    dyb = np.diff(yb) / yb_m
    dys = np.diff(close) / (np.mean(close) + 1)  # dummy
    fiz_net = (fb - fs) / toi * 100
    dfn = np.diff(fiz_net)
    dtoi = np.diff(toi) / toi_m
    
    return {
        'dates': dates, 'opn': opn, 'high': high, 'low': low, 'close': close,
        'dv': dv, 'dyb': dyb, 'dys': dys, 'dfn': dfn, 'dtoi': dtoi,
        'n': len(rows), 'vol': vol, 'label': label
    }

print('=== АУДИТ КАНДИДАТОВ ===')
print()

for ticker, pname, cs in CANDIDATES:
    print(f'--- {ticker} | {pname} | cs={cs} ---')
    
    # 1. Основные данные 2024-2026
    d = get_data(ticker, '2024-01-01', '2026-05-01')
    if d is None:
        print('  Нет данных')
        continue
    
    pfunc = PF[pname]
    
    # 2. LOOK-AHEAD AUDIT
    # Проверяем: сигнал на day i, entry на open[i+1] — не используем ли close[i+1]?
    lookahead_errors = 0
    sig_count = 0
    for i in range(1, d['n'] - HOLD - 2):
        if i >= len(d['dv']):
            break
        if not pfunc(d['dv'][i], d['dyb'][i], d['dys'][i], d['dfn'][i], d['dtoi'][i]):
            continue
        sig_count += 1
        ei = i + 1
        ep = float(d['opn'][ei])
        # Если entry_price совпадает с close[i] — это look-ahead, неправильно
        if abs(ep - float(d['close'][i])) < 0.01:
            lookahead_errors += 1
    
    print(f'  Сигналов: {sig_count}, look-ahead ошибок: {lookahead_errors} {"❌" if lookahead_errors > 0 else "✅"}')
    
    # 3. B&H COMPARISON (1 контракт, без реинвеста)
    close = d['close']
    bnh_1c = (close[-1] - close[0]) * cs
    
    # Стратегия 1 контракт
    sig_pnl = 0
    sig_trades = 0
    for i in range(1, d['n'] - HOLD - 2):
        if i >= len(d['dv']):
            break
        if not pfunc(d['dv'][i], d['dyb'][i], d['dys'][i], d['dfn'][i], d['dtoi'][i]):
            continue
        ei = i + 1
        xi = min(ei + HOLD, d['n'] - 2)
        if ei >= d['n'] - 2:
            continue
        ep = float(d['opn'][ei])
        xp = float(d['close'][xi])
        gp = cs * (xp - ep)
        cm = COMM
        sig_pnl += gp - cm
        sig_trades += 1
    
    print(f'  B&H 1 контракт: {bnh_1c:+,.0f} RUB')
    print(f'  Стратегия 1 контракт: {sig_pnl:+,.0f} RUB ({sig_trades} сделок)')
    
    # Если стратегия > B&H — может быть edge. Если < — ловит тренд хуже случайного
    edge_ratio = sig_pnl / bnh_1c * 100 if bnh_1c != 0 else 0
    print(f'  Эффективность vs B&H: {edge_ratio:.0f}%')
    print(f'  Средняя сделка: {sig_pnl/sig_trades:+,.0f} RUB' if sig_trades > 0 else '')
    
    # 4. ВНЕВЫБОРКА 2023 (если данные есть)
    d23 = get_data(ticker, '2023-01-01', '2023-12-31', '2023')
    if d23 and d23['n'] >= 30:
        sig_pnl_23 = 0
        sig_trades_23 = 0
        for i in range(1, d23['n'] - HOLD - 2):
            if i >= len(d23['dv']):
                break
            if not pfunc(d23['dv'][i], d23['dyb'][i], d23['dys'][i], d23['dfn'][i], d23['dtoi'][i]):
                continue
            ei = i + 1
            xi = min(ei + HOLD, d23['n'] - 2)
            if ei >= d23['n'] - 2:
                continue
            ep = float(d23['opn'][ei])
            xp = float(d23['close'][xi])
            gp = cs * (xp - ep)
            cm = COMM
            sig_pnl_23 += gp - cm
            sig_trades_23 += 1
        
        bnh_23 = (d23['close'][-1] - d23['close'][0]) * cs
        print(f'  Вневыборка 2023: {sig_trades_23} сделок, PnL={sig_pnl_23:+,.0f} (B&H: {bnh_23:+,.0f})')
        
        if sig_trades_23 >= 5:
            if sig_pnl_23 > 0:
                print(f'  ✅ Вневыборка: положительная')
            else:
                print(f'  ❌ Вневыборка: отрицательная — стратегия не работает на новых данных')
    else:
        print(f'  2023: нет данных для вневыборки')
    
    print()

print('=== АУДИТ ЗАВЕРШЁН ===')
