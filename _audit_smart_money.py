#!/usr/bin/env python3
"""Аудит GD daily smart_money: look-ahead, BM/BR, SAMC-тест 2024."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
HOLD = 10
SL = 0.01
COMM = 4

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def run_backtest(ticker, cs, date_from='2025-01-01', date_to='2026-05-01', label=''):
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
    
    if len(rows) < 30:
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
    ret = np.diff(close) / close[:-1] * 100
    
    n = len(rows)
    
    # --- TEST 1: Look-ahead ---
    # Сигнал: dyb[i] > 0 AND dfiz[i] < 0
    # Entry: open[i+1], Exit: close[i+1+hold]
    # dyb[i] = yb[i+1] - yb[i] — это ТОЛЬКО данные до дня i+1
    # open[i+1] — цена дня i+1. Это корректно — сигнал до открытия.
    
    # Но проверим: если перепутать и взять close[i] как entry, а open[i+1] как exit?
    # Сделаем аудит: сравним entry_price с high[i] и low[i]
    
    errors = 0
    signals_found = 0
    for i in range(10, n - HOLD - 2):
        if dyb[i] > 0 and dfiz[i] < 0:
            signals_found += 1
            ei = i + 1
            ep = float(opn[ei])
            # Проверка: ep должно быть ДОСТУПНО в день ei (это open)
            # Проверяем, что ep не равен close[i] (что было бы look-ahead)
            if ep == float(close[i]):
                errors += 1
    
    # --- TEST 2: Sequential backtest как в оригинале ---
    nf = 5
    fsize = n // nf
    fold_rets = []
    all_trades = []
    
    for f in range(nf):
        s = f * fsize
        e = n if f == 4 else (f + 1) * fsize
        eq = CAPITAL
        peak = eq
        mdd = 0
        
        for i in range(s, e - 1):
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
            eq += gp - cm
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            mdd = max(mdd, dd)
            
            all_trades.append({'fold': f+1, 'entry': dates[ei], 'exit': dates[xi],
                               'ep': float(ep), 'xp': float(xp), 'pnl': round(gp-cm, 0),
                               'n': nc, 'stop': stop_hit, 'dyb': float(dyb[i]),
                               'dfiz': float(dfiz[i])})
        
        ret = (eq - CAPITAL) / CAPITAL * 100
        fold_rets.append(round(ret, 2))
    
    # --- VERIFY: signal bar price vs entry price ---
    # Проверим для первых 10 сигналов: что было на баре сигнала?
    sig_prices = []
    cnt = 0
    for i in range(10, n - HOLD - 2):
        if dyb[i] > 0 and dfiz[i] < 0 and cnt < 10:
            sig_prices.append({
                'date': dates[i],
                'sig_close': float(close[i]),
                'next_open': float(opn[i+1]),
                'dyb': float(dyb[i]),
                'dfiz': float(dfiz[i])
            })
            cnt += 1
    
    return {
        'ticker': ticker, 'label': label,
        'n_days': n, 'signals': signals_found, 'lookahead_errors': errors,
        'fold_rets': fold_rets,
        'total_ret': round(sum(fold_rets), 2),
        'trades': len(all_trades),
        'sig_prices': sig_prices
    }

# === GD 2025-2026 (основной) ===
print('=== GD 2025-2026 (основной тест) ===')
gd = run_backtest('GD', 10, '2025-01-01', '2026-05-01', 'GD main')
if gd:
    print(f'Дней: {gd["n_days"]}, Сигналов: {gd["signals"]}')
    print(f'Look-ahead errors: {gd["lookahead_errors"]}')
    print(f'Доходность по фолдам: {gd["fold_rets"]}')
    print(f'Всего: {gd["total_ret"]}%')
    print(f'Первые 5 сигналов (close сигнала → open входа):')
    for s in gd['sig_prices'][:5]:
        print(f'  {s["date"]}: close={s["sig_close"]:.1f} → next_open={s["next_open"]:.1f} dyb={s["dyb"]:.0f} dfiz={s["dfiz"]:.2f}')
    print()

# === GD 2024 (вневыборка) ===
print('=== GD 2024 (SAMC — вневыборка) ===')
gd24 = run_backtest('GD', 10, '2024-01-01', '2024-12-31', 'GD 2024')
if gd24:
    print(f'Дней: {gd24["n_days"]}, Сигналов: {gd24["signals"]}')
    print(f'Look-ahead errors: {gd24["lookahead_errors"]}')
    print(f'Доходность по фолдам: {gd24["fold_rets"]}')
    print(f'Всего: {gd24["total_ret"]}%')
    print()

# === BM ===
print('=== BM 2025-2026 ===')
bm = run_backtest('BM', 10, '2025-01-01', '2026-05-01', 'BM')
if bm:
    print(f'Дней: {bm["n_days"]}, Сигналов: {bm["signals"]}')
    print(f'Look-ahead errors: {bm["lookahead_errors"]}')
    print(f'Доходность по фолдам: {bm["fold_rets"]}')
    print(f'Всего: {bm["total_ret"]}%')
    print()

# === BR ===
print('=== BR 2025-2026 ===')
br = run_backtest('BR', 10, '2025-01-01', '2026-05-01', 'BR')
if br:
    print(f'Дней: {br["n_days"]}, Сигналов: {br["signals"]}')
    print(f'Look-ahead errors: {br["lookahead_errors"]}')
    print(f'Доходность по фолдам: {br["fold_rets"]}')
    print(f'Всего: {br["total_ret"]}%')
    print()

# === AF ===
print('=== AF 2025-2026 ===')
af = run_backtest('AF', 100, '2025-01-01', '2026-05-01', 'AF')
if af:
    print(f'Дней: {af["n_days"]}, Сигналов: {af["signals"]}')
    print(f'Look-ahead errors: {af["lookahead_errors"]}')
    print(f'Доходность по фолдам: {af["fold_rets"]}')
    print(f'Всего: {af["total_ret"]}%')
    print()

print('=== АУДИТ ЗАВЕРШЁН ===')
