#!/usr/bin/env python3
"""Систематический поиск паттернов: объём + OI + цена, все тикеры, дневки.
Без предвзятости — проверяем ВСЕ комбинации направлений."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000  # для расчёта, не реинвест, 1 контракт
HOLD = 5
SL = 0.0  # без стопа для чистоты эксперимента

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# Все символы с достаточным количеством данных
rows_symbols = ch.query("""
    SELECT symbol, count(*) as cnt
    FROM moex.prices_5m_oi
    WHERE time >= '2024-01-01'
    GROUP BY symbol
    HAVING cnt > 1000
    ORDER BY cnt DESC
""").result_rows

all_symbols = [r[0] for r in rows_symbols]
print(f'Символов с данными: {len(all_symbols)}')

# Паттерны: комбинации направлений (d_volume, d_yb, d_ys, d_fiznet, d_toi)
# Каждый: +1 (вырос), -1 (упал), 0 (без изменений)
# Но 0 в OI редко — там почти всегда изменение
# Упрощаем: смотрим sign изменений

patterns = [
    # Расхождение объём/OI
    ('vol_up + oi_up + yb_up',           lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0),
    ('vol_up + oi_down',                 lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0),
    ('vol_down + oi_up',                 lambda dv, dyb, dys, dfn, dtoi: dv < 0 and dtoi > 0),
    ('vol_up + yb_up + fiz_down',        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0),
    ('vol_up + yb_down + fiz_up',        lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0),
    # Экстремумы fiz_net
    ('fiz_net_extreme + vol_up',         lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5),
    ('fiz_net_extreme + vol_down',       lambda dv, dyb, dys, dfn, dtoi: dv < 0 and abs(dfn) > 5),
    # Чистый OI сдвиг
    ('yb_up + ys_down',                  lambda dv, dyb, dys, dfn, dtoi: dyb > 0 and dys < 0),
    ('yb_down + ys_up',                  lambda dv, dyb, dys, dfn, dtoi: dyb < 0 and dys > 0),
    # Объём без OI (чистая ликвидность)
    ('vol_up + oi_flat',                 lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dtoi) < 0.01 * np.mean([1])),
    # Контрольный: просто случайный
    ('random_control',                   lambda dv, dyb, dys, dfn, dtoi: True),
]

results = []

for sym in all_symbols:
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': sym}).result_rows
    
    if len(rows) < 60:
        continue
    
    close = np.array([r[1] for r in rows], dtype=float)
    vol = np.array([r[2] for r in rows], dtype=float)
    yb = np.array([r[3] for r in rows], dtype=float)
    ys = np.array([r[4] for r in rows], dtype=float)
    fb = np.array([r[5] for r in rows], dtype=float)
    fs = np.array([r[6] for r in rows], dtype=float)
    toi = np.array([r[7] for r in rows], dtype=float)
    toi = np.where(toi <= 0, 1, toi)
    
    # Нормированные изменения (%, а не абсолют)
    dv = np.diff(vol) / (np.mean(vol) + 1)
    dyb = np.diff(yb) / (np.mean(yb) + 1)
    dys = np.diff(ys) / (np.mean(ys) + 1)
    dtoi = np.diff(toi) / (np.mean(toi) + 1)
    fiz_net = (fb - fs) / toi * 100
    dfn = np.diff(fiz_net)
    
    ret = np.diff(close) / close[:-1] * 100
    n = len(ret)
    
    for pname, pfunc in patterns:
        sigs = 0
        pnl_sum = 0
        wins = 0
        
        for i in range(1, n - HOLD):
            if i >= len(dv) or i >= len(dyb):
                break
            if not pfunc(dv[i], dyb[i], dys[i], dfn[i], dtoi[i]):
                continue
            
            sigs += 1
            future_ret = ret[min(i + HOLD, n - 1)]
            pnl_sum += future_ret
            if future_ret > 0:
                wins += 1
        
        if sigs >= 10:
            avg_pnl = pnl_sum / sigs
            wr = wins / sigs * 100
            results.append((sym, pname, sigs, round(avg_pnl, 3), round(wr, 0)))

# Топ по средней доходности
print(f'=== ТОП-30 паттернов (hold={HOLD}) ===')
print(f'{"Symbol":>8} {"Pattern":>35} {"Sig":>5} {"AvgRet%":>8} {"WR%":>5}')
print('-' * 65)
results.sort(key=lambda x: -x[3])
for sym, pname, cnt, avg, wr in results[:30]:
    print(f'{sym:>8} {pname:>35} {cnt:>5} {avg:>+8.3f} {wr:>5.0f}')

print()

# А теперь: какие паттерны стабильно работают на МНОГИХ тикерах?
print('=== Паттерны, работающие на 3+ тикерах ===')
pattern_tickers = {}
for sym, pname, cnt, avg, wr in results:
    if avg > 0 and wr > 55:
        pattern_tickers.setdefault(pname, []).append((sym, cnt, avg, wr))

for pname in sorted(pattern_tickers.keys(), key=lambda x: -len(pattern_tickers[x])):
    tkrs = pattern_tickers[pname]
    if len(tkrs) >= 3:
        print(f'{pname:>35}: {len(tkrs)} тикеров')
        for sym, cnt, avg, wr in tkrs[:5]:
            print(f'  {sym:>8}: sig={cnt:>4}, avg={avg:+.3f}%, WR={wr:.0f}%')
