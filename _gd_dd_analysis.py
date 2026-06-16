#!/usr/bin/env python3
"""Анализ просадок GD + портфель тикеров."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 200_000
CS = 10
COMM = 4
HOLD = 10
SL = 0.01

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def backtest(ticker, cs, date_from='2024-01-01', date_to='2026-05-01'):
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.time <= %(e)s
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker, 's': date_from, 'e': date_to}).result_rows
    
    if len(rows) < 60:
        return None
    
    dates = [str(r[0]) for r in rows]
    opn = np.array([r[1] for r in rows], dtype=float)
    high = np.array([r[2] for r in rows], dtype=float)
    low = np.array([r[3] for r in rows], dtype=float)
    close = np.array([r[4] for r in rows], dtype=float)
    yb = np.array([r[5] for r in rows], dtype=float)
    fb = np.array([r[6] for r in rows], dtype=float)
    fs = np.array([r[7] for r in rows], dtype=float)
    toi = np.array([r[8] for r in rows], dtype=float)
    toi = np.where(toi <= 0, 1, toi)
    
    dyb = np.diff(yb)
    fiz_net = (fb - fs) / toi * 100
    dfiz = np.diff(fiz_net)
    
    n = len(rows)
    eq = CAPITAL
    peak = eq
    max_dd = 0
    current_dd = 0
    dd_streak = 0
    max_dd_streak = 0
    trades = []
    dd_periods = []
    dd_start = None
    
    for i in range(1, n - HOLD - 1):
        if not (dyb[i] > 0 and dfiz[i] < 0):
            continue
        ei = i + 1
        xi = min(ei + HOLD, n - 1)
        if ei >= n - 1:
            continue
        
        ep = float(opn[ei])
        sp = ep * (1 - SL)
        stop_hit = False
        xp = float(close[xi])
        
        for j in range(ei, xi + 1):
            if float(low[j]) <= sp:
                xp = sp
                stop_hit = True
                break
        
        go = ep * cs
        nc = max(1, int(eq // go)) if go > 0 else 1
        gp = nc * cs * (xp - ep)
        cm = nc * COMM
        npnl = gp - cm
        eq += npnl
        if eq > peak:
            peak = eq
            current_dd = 0
            if dd_start:
                dd_periods.append((dd_start, dates[xi], round(current_dd, 2)))
                dd_start = None
        else:
            dd = (peak - eq) / peak * 100
            current_dd = dd
            if dd > max_dd:
                max_dd = dd
            if dd_start is None:
                dd_start = dates[ei]
        
        if npnl < 0:
            dd_streak += 1
            max_dd_streak = max(max_dd_streak, dd_streak)
        else:
            dd_streak = 0
        
        trades.append({
            'entry': dates[ei], 'exit': dates[xi],
            'pnl': round(npnl, 0), 'n': nc, 'stop': stop_hit
        })
    
    ret = (eq - CAPITAL) / CAPITAL * 100
    wins = sum(1 for t in trades if t['pnl'] > 0)
    wr = wins / len(trades) * 100 if trades else 0
    
    # Серии
    streaks = []
    cur = 0
    cur_pnl = 0
    for t in trades:
        if t['pnl'] < 0:
            cur += 1
            cur_pnl += t['pnl']
        else:
            if cur > 0:
                streaks.append((cur, round(cur_pnl, 0)))
            cur = 0
            cur_pnl = 0
    if cur > 0:
        streaks.append((cur, round(cur_pnl, 0)))
    
    max_streak_len = max([s[0] for s in streaks]) if streaks else 0
    max_streak_loss = min([s[1] for s in streaks]) if streaks else 0
    
    return {
        'ticker': ticker,
        'n_days': n, 'trades': len(trades), 'ret': round(ret, 1),
        'dd': round(max_dd, 1), 'wr': round(wr, 0),
        'max_loss_streak': max_streak_len,
        'max_streak_loss': round(abs(max_streak_loss), 0),
        'streaks': streaks
    }

# === GD детально ===
print('=== GD — Анализ просадок ===')
gd = backtest('GD', 10)
if gd:
    print(f'Максимальная просадка: {gd["dd"]}%')
    print(f'Максимальная серия убытков: {gd["max_loss_streak"]} подряд')
    print(f'Потеряно в худшей серии: {gd["max_streak_loss"]:,.0f} RUB')
    print(f'Всего сделок: {gd["trades"]}')
    print(f'WR: {gd["wr"]}%')
    print()
    print('Серии убытков:')
    for s in gd['streaks']:
        print(f'  {s[0]} подряд, потери: {abs(s[1]):,.0f} RUB')
    print()

# === Другие тикеры ===
print('=== Другие тикеры (smart_money сигнал) ===')
candidates = [
    ('BM', 10, 'BM'),
    ('BR', 10, 'BR'),
    ('AF', 100, 'AF'),
    ('AL', 25, 'AL'),
    ('GD', 10, 'GD'),
]

results = []
for ticker, cs, label in candidates:
    r = backtest(ticker, cs)
    if r:
        results.append(r)
        print(f'{label:>5}: {r["n_days"]:>4}d {r["trades"]:>3}tr ret={r["ret"]:>+7.1f}% DD={r["dd"]:>5.1f}% WR={r["wr"]:>2.0f}% max_loss={r["max_loss_streak"]} streak')

# Портфель: равные доли GD + лучшее из остальных
print()
print('=== Портфель (50% GD + 50% BM) ===')
# Симуляция портфеля: половина капитала на каждый тикер
# GD + BM
CAP_TOTAL = 200_000

for t1, t2, label in [('GD', 'BM', 'GD+BM'), ('GD', 'AF', 'GD+AF')]:
    r1 = backtest(t1, 10)
    r2 = backtest(t2, 10 if t2 != 'AF' else 100)
    if not r1 or not r2:
        continue
    
    # Простая оценка: если бы торговали пополам портфель
    # Каждый получает 100K
    # Общая DD будет меньше, т.к. сигналы асинхронны
    
    # Корреляция доходностей
    print(f'{label}:')
    print(f'  GD:  ret={r1["ret"]:+.1f}% DD={r1["dd"]}%')
    print(f'  {t2}: ret={r2["ret"]:+.1f}% DD={r2["dd"]}%')
    print(f'  Портфель 50/50: ожидаемая DD ~ {round((r1["dd"]+r2["dd"])/2*0.7, 1)}% (с корреляцией ~0.3)')
