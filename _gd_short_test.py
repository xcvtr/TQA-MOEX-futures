#!/usr/bin/env python3
"""GD SHORT тест и сравнение LONG/SHORT."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 200_000
CS = 10
COMM = 4

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
n = len(rows)

def run(cond, hold, sl_pct, direction):
    """direction=1 LONG, -1 SHORT"""
    eq = CAPITAL
    peak = eq
    mdd = 0
    trades = []
    
    for i in range(1, n - hold - 1):
        if not cond(i):
            continue
        ei = i + 1
        xi = min(ei + hold, n - 1)
        if ei >= n - 1:
            continue
        
        ep = float(opn[ei])
        
        if direction == 1:
            sp = ep * (1 - sl_pct)
            stop_hit = False
            xp = float(close[xi])
            for j in range(ei, xi + 1):
                if float(low[j]) <= sp:
                    xp = sp
                    stop_hit = True
                    break
            gp = (xp - ep) * CS
        else:
            sp = ep * (1 + sl_pct)
            stop_hit = False
            xp = float(close[xi])
            for j in range(ei, xi + 1):
                if float(high[j]) >= sp:
                    xp = sp
                    stop_hit = True
                    break
            gp = (ep - xp) * CS  # SHORT
        
        nc = max(1, int(eq // (ep * CS))) if ep * CS > 0 else 1
        cm = nc * COMM
        npnl = gp * nc - cm
        eq += npnl
        
        trades.append({
            'entry': dates[ei], 'exit': dates[xi],
            'pnl': round(npnl, 0), 'n': nc, 'stop': stop_hit
        })
        
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        mdd = max(mdd, dd)
    
    ret = (eq - CAPITAL) / CAPITAL * 100
    wins = sum(1 for t in trades if t['pnl'] > 0)
    wr = wins / len(trades) * 100 if trades else 0
    tot_pnl = sum(t['pnl'] for t in trades)
    
    return ret, mdd, wr, len(trades)

print('=== GD SHORT (yb↓ + fiz↑) ===')
for hold in [3, 5, 7, 10]:
    for sl in [0.005, 0.01, 0.02]:
        ret, mdd, wr, cnt = run(lambda i: dyb[i] < 0 and dfiz[i] > 0, hold, sl, -1)
        if cnt >= 5:
            print(f'  hold={hold:>2} sl={sl:.1%}: {cnt:>3}tr ret={ret:>+7.1f}% DD={mdd:>5.1f}% WR={wr:>3.0f}%')

print()
print('=== GD LONG (yb↑ + fiz↓) — эталон ===')
for hold in [10]:
    for sl in [0.01]:
        ret, mdd, wr, cnt = run(lambda i: dyb[i] > 0 and dfiz[i] < 0, hold, sl, 1)
        print(f'  hold={hold:>2} sl={sl:.1%}: {cnt:>3}tr ret={ret:>+7.1f}% DD={mdd:>5.1f}% WR={wr:>3.0f}%')

print()
print('=== LONG+SHORT портфель (сигнал в обе стороны) ===')
# Одновременно: если yb↑+fiz↓ → LONG, если yb↓+fiz↑ → SHORT
# Половина капитала на каждую сторону
eq_l = CAPITAL // 2
eq_s = CAPITAL // 2
peak = CAPITAL
mdd = 0
all_trades = []

for i in range(1, n - 10 - 1):
    for side, cond, epx in [('L', lambda: dyb[i] > 0 and dfiz[i] < 0, opn), ('S', lambda: dyb[i] < 0 and dfiz[i] > 0, opn)]:
        if not (side == 'L' and dyb[i] > 0 and dfiz[i] < 0) and not (side == 'S' and dyb[i] < 0 and dfiz[i] > 0):
            continue
        ei = i + 1
        xi = min(ei + 10, n - 1)
        if ei >= n - 1:
            continue
        ep = float(opn[ei])
        sp = ep * (1 - 0.01) if side == 'L' else ep * (1 + 0.01)
        stop_hit = False
        xp = float(close[xi])
        for j in range(ei, xi + 1):
            if side == 'L' and float(low[j]) <= sp:
                xp = sp; stop_hit = True; break
            if side == 'S' and float(high[j]) >= sp:
                xp = sp; stop_hit = True; break
        
        eq_use = eq_l if side == 'L' else eq_s
        nc = max(1, int(eq_use // (ep * CS))) if ep * CS > 0 else 1
        gp = nc * CS * ((xp - ep) if side == 'L' else (ep - xp))
        cm = nc * COMM
        npnl = gp - cm
        
        if side == 'L':
            eq_l += npnl
        else:
            eq_s += npnl
        
        if eq_l + eq_s > peak:
            peak = eq_l + eq_s
        dd = (peak - (eq_l + eq_s)) / peak * 100
        mdd = max(mdd, dd)
        all_trades.append((side, npnl))

total_eq = eq_l + eq_s
ret = (total_eq - CAPITAL) / CAPITAL * 100
wins = sum(1 for _, p in all_trades if p > 0)
print(f'  LONG+SHORT: {len(all_trades)}tr ret={ret:+.1f}% DD={mdd:.1f}% WR={wins/len(all_trades)*100:.0f}%')
