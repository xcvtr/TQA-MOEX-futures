#!/usr/bin/env python3
"""GD портфель: 2 паттерна, walk-forward, реинвест."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 200_000
CS = 10
COMM = 4
HOLD = 5

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

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
    WHERE p.symbol = 'GD' AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

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
fiz_net = (fb - fs) / toi * 100
dfn = np.diff(fiz_net)
dtoi = np.diff(toi) / toi_m

n = len(rows)
n_folds = 4
fsize = n // n_folds

# Паттерны
patterns = {
    'vol_up_oi_down': lambda i: dv[i] > 0 and dtoi[i] < 0,
    'vol_up_yb_up_fiz_down': lambda i: dv[i] > 0 and dyb[i] > 0 and dfn[i] < 0,
}

# Поиск всех hold и sl
print('=== GD Портфель: grid search ===')
print()

all_results = []

for hold in [2, 3, 5, 7, 10]:
    for sl in [0.0, 0.01, 0.02, 0.03, 0.05]:
        for mode in ['p1', 'p2', 'p1_or_p2', 'p1_and_p2', 'p1_then_p2']:
            fold_rets = []
            fold_dds = []
            
            for f in range(n_folds):
                s = f * fsize
                e = n if f == 4 else (f + 1) * fsize
                
                eq = CAPITAL
                peak = eq
                mdd = 0
                trades = 0
                
                # Для портфеля: половина капитала на каждый паттерн
                eq_p1 = CAPITAL // 2 if mode in ['p1_then_p2', 'p1_or_p2'] else CAPITAL
                eq_p2 = CAPITAL // 2 if mode in ['p1_then_p2', 'p1_or_p2'] else CAPITAL
                
                for i in range(s, min(e, n - hold - 2)):
                    if i >= len(dv):
                        break
                    
                    sig1 = patterns['vol_up_oi_down'](i)
                    sig2 = patterns['vol_up_yb_up_fiz_down'](i)
                    
                    enter = False
                    use_p1 = False
                    use_p2 = False
                    
                    if mode == 'p1' and sig1:
                        enter = True; use_p1 = True
                    elif mode == 'p2' and sig2:
                        enter = True; use_p2 = True
                    elif mode == 'p1_or_p2' and (sig1 or sig2):
                        enter = True
                        if sig1: use_p1 = True
                        if sig2: use_p2 = True
                    elif mode == 'p1_and_p2' and sig1 and sig2:
                        enter = True; use_p1 = True; use_p2 = True
                    elif mode == 'p1_then_p2':
                        # p1 даёт сигнал → входим; если до выхода p2 дал сигнал → добавляем
                        if sig1:
                            enter = True; use_p1 = True
                    
                    if not enter:
                        continue
                    
                    ei = i + 1
                    xi = min(ei + hold, n - 2)
                    if ei >= n - 2:
                        continue
                    
                    for side_name, use_flag in [('p1', use_p1), ('p2', use_p2)]:
                        if not use_flag:
                            continue
                        
                        eq_use = eq_p1 if side_name == 'p1' else eq_p2
                        if eq_use <= 0:
                            continue
                        
                        ep = float(opn[ei])
                        
                        if sl > 0:
                            sp = ep * (1 - sl)
                            stop_hit = False
                            xp = float(close[xi])
                            for j in range(ei, xi + 1):
                                if float(low[j]) <= sp:
                                    xp = sp
                                    break
                        else:
                            xp = float(close[xi])
                        
                        go = ep * CS
                        nc = max(1, int(eq_use // go)) if go > 0 else 1
                        gp = nc * CS * (xp - ep)
                        cm = nc * COMM
                        npnl = gp - cm
                        
                        if side_name == 'p1':
                            eq_p1 += npnl
                        else:
                            eq_p2 += npnl
                        
                        trades += 1
                
                eq = eq_p1 + eq_p2 if mode in ['p1_then_p2', 'p1_or_p2'] else (eq_p1 if mode == 'p1' else eq_p2)
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100
                mdd = max(mdd, dd)
                
                ret = (eq - CAPITAL) / CAPITAL * 100
                fold_rets.append(round(ret, 1))
                fold_dds.append(round(mdd, 1))
            
            mean_ret = np.mean(fold_rets)
            mean_dd = np.mean(fold_dds)
            min_ret = min(fold_rets)
            max_dd = max(fold_dds)
            neg = sum(1 for r in fold_rets if r < 0)
            score = (mean_ret / mean_dd if mean_dd > 0 else 0) * (1 - neg / n_folds * 0.5)
            
            if mean_ret > 5 and mean_dd < 20 and neg <= 1:
                all_results.append({
                    'mode': mode, 'hold': hold, 'sl': sl,
                    'mean_ret': mean_ret, 'mean_dd': mean_dd,
                    'min_ret': min_ret, 'max_dd': max_dd,
                    'neg': neg, 'score': round(score, 2),
                    'rets': fold_rets, 'dds': fold_dds
                })

all_results.sort(key=lambda x: -x['score'])
print(f'Найдено {len(all_results)} комбинаций')
print()

print(f'{"Mode":>15} {"Hold":>4} {"SL":>5} {"MeanRet":>8} {"MeanDD":>7} {"MinRet":>7} {"MaxDD":>7} {"Neg":>4} {"Score":>7}')
print('-' * 65)
for r in all_results[:20]:
    print(f'{r["mode"]:>15} {r["hold"]:>4} {r["sl"]:.0%} {r["mean_ret"]:>+7.1f}% {r["mean_dd"]:>6.1f}% {r["min_ret"]:>+6.1f}% {r["max_dd"]:>6.1f}% {r["neg"]:>4} {r["score"]:>7.2f}')

print()
print('=== Лучшие по каждому режиму ===')
for mode in ['p1', 'p2', 'p1_or_p2', 'p1_and_p2']:
    mode_results = [r for r in all_results if r['mode'] == mode]
    if mode_results:
        best = mode_results[0]
        print(f'{mode:>15}: hold={best["hold"]} sl={best["sl"]:.0%} ret={best["mean_ret"]:+.1f}% dd={best["mean_dd"]:.1f}% min={best["min_ret"]:+.1f}% score={best["score"]:.2f}')

# Сохраняем
with open('reports/gd_portfolio_grid.json', 'w') as f:
    json.dump(all_results[:50], f, indent=2, default=str)
print(f'\nSaved to reports/gd_portfolio_grid.json')
