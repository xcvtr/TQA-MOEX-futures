#!/usr/bin/env python3
"""GD smart_money — итоговый отчёт по 200K с реинвестом."""
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
    WHERE p.symbol = 'GD' AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

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
ret = np.diff(close) / close[:-1] * 100

n = len(rows)

# Full run
eq = CAPITAL
eq_curve = [eq]
dates_curve = [dates[0]]
peak = eq
mdd = 0
mdd_start = dates[0]
mdd_end = dates[0]
trades = []

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
    
    go = ep * CS
    nc = max(1, int(eq // go)) if go > 0 else 1
    gp = nc * CS * (xp - ep)
    cm = nc * COMM
    npnl = gp - cm
    eq += npnl
    eq_curve.append(eq)
    dates_curve.append(dates[xi])
    if eq > peak:
        peak = eq
    dd = (peak - eq) / peak * 100
    if dd > mdd:
        mdd = dd
        mdd_end = dates[xi]
    mdd = max(mdd, dd)
    
    trades.append({
        'entry': dates[ei], 'exit': dates[xi],
        'ep': float(ep), 'xp': float(xp),
        'pnl': round(npnl, 0), 'n': nc,
        'stop': stop_hit
    })

total_ret = (eq - CAPITAL) / CAPITAL * 100
wins = sum(1 for t in trades if t['pnl'] > 0)
wr = wins / len(trades) * 100 if trades else 0
tot_comm = sum(4 * t['n'] for t in trades)
avg_win = sum(t['pnl'] for t in trades if t['pnl'] > 0) / wins if wins > 0 else 0
losses = [t['pnl'] for t in trades if t['pnl'] <= 0]
avg_loss = abs(sum(losses)) / len(losses) if losses else 0
pf = abs(sum(t['pnl'] for t in trades if t['pnl'] > 0) / (abs(sum(t['pnl'] for t in trades if t['pnl'] < 0)) + 1))
calmar = total_ret / mdd if mdd > 0 else 0

# Sharpe
eq_arr = np.array(eq_curve)
daily_rets = np.diff(eq_arr) / eq_arr[:-1]
sharpe = np.mean(daily_rets) / (np.std(daily_rets) + 1e-10) * np.sqrt(252) if len(daily_rets) > 1 else 0

print(f'=== GD smart_money — {CAPITAL:,} RUB, реинвест ===')
print(f'Период: {dates[0]} – {dates[-1]} ({n} дней)')
print()
print(f'Начальный капитал: {CAPITAL:>10,.0f} RUB')
print(f'Конечный капитал:   {eq:>10,.0f} RUB')
print(f'Общая доходность:   {total_ret:>+8.1f}%')
print(f'Max просадка:       {mdd:>7.1f}%')
print(f'Calmar ratio:       {calmar:>7.2f}')
print(f'Sharpe (год):       {sharpe:>7.2f}')
print(f'Сделок:             {len(trades):>10}')
print(f'Win Rate:           {wr:>7.0f}%')
print(f'Profit Factor:      {pf:>7.2f}')
print(f'Средняя прибыль:    {avg_win:>10,.0f} RUB')
print(f'Средний убыток:     {avg_loss:>10,.0f} RUB')
print(f'Комиссий всего:     {tot_comm:>10,.0f} RUB')
print(f'Комиссий % от кап:  {tot_comm/CAPITAL*100:>7.2f}%')
print()

# По годам
print('=== По годам ===')
years = {}
for t in trades:
    y = t['entry'][:4]
    if y not in years:
        years[y] = {'pnl': 0, 'wins': 0, 'losses': 0, 'cnt': 0}
    years[y]['pnl'] += t['pnl']
    years[y]['cnt'] += 1
    if t['pnl'] > 0:
        years[y]['wins'] += 1

for y in sorted(years):
    yd = years[y]
    wr_y = yd['wins'] / yd['cnt'] * 100
    print(f'  {y}: {yd["cnt"]:>3} сделок, PnL={yd["pnl"]:>+10,.0f} RUB, WR={wr_y:.0f}%')

# По месяцам
print()
print('=== По месяцам ===')
m_pnl = {}
for t in trades:
    m = t['entry'][:7]
    m_pnl.setdefault(m, []).append(t['pnl'])

for m in sorted(m_pnl):
    pnls = m_pnl[m]
    cnt = len(pnls)
    s = sum(pnls)
    print(f'  {m}: {cnt:>2} сделок, PnL={s:>+10,.0f} RUB')
