#!/usr/bin/env python3
"""Сравнение GD SmartMoney vs Buy & Hold."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

rows = ch.query("""
    SELECT toDate(p.time) as d,
           argMax(p.open, p.time) as open,
           argMax(p.close, p.time) as close
    FROM moex.prices_5m p
    WHERE p.symbol = 'GD' AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

dates = [str(r[0]) for r in rows]
opn = np.array([r[1] for r in rows], dtype=float)
close = np.array([r[2] for r in rows], dtype=float)
n = len(rows)

# Buy & Hold
first_px = close[0]
last_px = close[-1]
bnh_ret = (last_px - first_px) / first_px * 100

# B&H drawdown
peak = close[0]
bnh_dd = 0
for c in close:
    if c > peak:
        peak = c
    dd = (peak - c) / peak * 100
    bnh_dd = max(bnh_dd, dd)

# B&H на 200K
cap = 200000
cs = 10
go0 = first_px * cs
n_con = int(cap // go0) if go0 > 0 else 1
final_bnh = n_con * cs * last_px + (cap - n_con * go0)
bnh_pnl = final_bnh - cap

print(f'=== GD Buy & Hold (2024-01 – 2026-05) ===')
print(f'Цена: {first_px:.0f} → {last_px:.0f}')
print(f'Доходность: {bnh_ret:+.1f}%')
print(f'Max DD: {bnh_dd:.1f}%')
print(f'B&H на 200K: {final_bnh:,.0f} RUB (PnL {bnh_pnl:+,.0f})')
print()

# Дневной drift
daily_ret = np.diff(close) / close[:-1] * 100
mean_drift = np.mean(daily_ret)
annual_drift = (1 + mean_drift/100) ** 252 - 1
up_pct = sum(1 for r in daily_ret if r > 0) / len(daily_ret) * 100
print(f'Дневной drift: {mean_drift:+.3f}%')
print(f'Годовой drift: {annual_drift:+.0f}%')
print(f'UP days: {up_pct:.0f}%')

# А теперь: стратегия vs B&H
# Если стратегия просто ловит тренд, то:
# - сигналы должны быть случайно распределены по времени
# - и их доходность = B&H доходность за те же периоды
# Если есть edge — доходность стратегии > B&H за периоды сделок

# Посчитаем: сколько дней мы были в рынке
# 201 сделка × ~10 дней среднего удержания = ~2010 дней = ~40% времени
# Но с реинвестом каждый раз на полный капитал

# Доходность на 1 контракт (без реинвеста)
cap_single = first_px * cs
print(f'\n=== Без реинвеста (1 контракт) ===')

# Пересчёт: 1 контракт, 200K капитал, но не реинвестируем
# Просто складываем PnL
rows2 = ch.query("""
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

opn2 = np.array([r[1] for r in rows2], dtype=float)
high2 = np.array([r[2] for r in rows2], dtype=float)
low2 = np.array([r[3] for r in rows2], dtype=float)
close2 = np.array([r[4] for r in rows2], dtype=float)
yb2 = np.array([r[5] for r in rows2], dtype=float)
fb2 = np.array([r[6] for r in rows2], dtype=float)
fs2 = np.array([r[7] for r in rows2], dtype=float)
toi2 = np.array([r[8] for r in rows2], dtype=float)
toi2 = np.where(toi2 <= 0, 1, toi2)

dyb2 = np.diff(yb2)
fiz_net2 = (fb2 - fs2) / toi2 * 100
dfiz2 = np.diff(fiz_net2)

n2 = len(rows2)
HOLD = 10
SL = 0.01
COMM = 4

# 1 контракт, никакого реинвеста
pnl_single = 0
trades_single = []
for i in range(1, n2 - HOLD - 1):
    if not (dyb2[i] > 0 and dfiz2[i] < 0):
        continue
    ei = i + 1
    xi = min(ei + HOLD, n2 - 1)
    if ei >= n2 - 1:
        continue
    
    ep = float(opn2[ei])
    sp = ep * (1 - SL)
    stop_hit = False
    xp = float(close2[xi])
    
    for j in range(ei, xi + 1):
        if float(low2[j]) <= sp:
            xp = sp
            stop_hit = True
            break
    
    gp = cs * (xp - ep)
    cm = COMM
    pnl_single += gp - cm
    trades_single.append({
        'ep': float(ep), 'xp': float(xp),
        'pnl': round(gp-cm, 0),
        'bar_ret': (xp-ep)/ep*100
    })

mean_bar_ret = np.mean([t['bar_ret'] for t in trades_single])
mean_up_ret = np.mean([t['bar_ret'] for t in trades_single if t['pnl'] > 0])
mean_dn_ret = np.mean([t['bar_ret'] for t in trades_single if t['pnl'] < 0])

print(f'Сделок: {len(trades_single)}')
print(f'Общий PnL (1 контракт): {pnl_single:+,.0f} RUB')
print(f'Средняя доходность на сделку: {mean_bar_ret:+.2f}%')
print(f'Средняя прибыльная: {mean_up_ret:+.2f}%')
print(f'Средняя убыточная: {mean_dn_ret:+.2f}%')
print()

# Сравнение с B&H
print(f'=== ВЕРДИКТ ===')
print(f'B&H на 200K:          +{bnh_pnl:>+10,.0f} RUB ({bnh_ret:+.1f}%)')
print(f'SmartMoney 200K(reinv): +{(2100.2/100*200000):>+10,.0f} RUB (+2100.2%)')
print(f'SmartMoney 1contract:  +{pnl_single:>+10,.0f} RUB')
print()

# Если просто умножить B&H на 1 контракт 
# (сколько заработал 1 контракт за весь период = last_px - first_px) * cs
bnh_1c = (last_px - first_px) * cs
print(f'B&H 1 контракт:       +{bnh_1c:>+10,.0f} RUB')
print(f'SmartMoney vs B&H(1c): {pnl_single/bnh_1c*100:.0f}% от B&H')
print()

# Средняя доходность сделки vs средняя доходность случайного 10-дневного периода
print(f'Случайный 10-дневный период GD:')
# Берём все 10-дневные периоды подряд и считаем среднюю доходность
ten_day_rets = []
for i in range(n - HOLD):
    r = (close[i+HOLD] - close[i]) / close[i] * 100
    ten_day_rets.append(r)
avg_random = np.mean(ten_day_rets)
median_random = np.median(ten_day_rets)
print(f'  Средняя: {avg_random:+.2f}%')
print(f'  Медиана: {median_random:+.2f}%')
print(f'  WR: {sum(1 for r in ten_day_rets if r>0)/len(ten_day_rets)*100:.0f}%')
print()
print(f'Стратегия: avg={mean_bar_ret:+.2f}% (vs случай {avg_random:+.2f}%)')
print(f'  Превышение: {mean_bar_ret - avg_random:+.2f}% на сделку')
