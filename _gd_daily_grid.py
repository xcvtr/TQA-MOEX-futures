#!/usr/bin/env python3
"""GD daily OI: grid search walk-forward."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
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
           argMax(o.yur_sell, p.time) as yur_sell,
           argMax(o.fiz_buy, p.time) as fiz_buy,
           argMax(o.fiz_sell, p.time) as fiz_sell,
           argMax(o.total_oi, p.time) as total_oi
    FROM moex.prices_5m p
    INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
    WHERE p.symbol = 'GD' AND p.time >= '2025-01-01' AND p.time <= '2026-05-01'
    GROUP BY d ORDER BY d
""").result_rows

dates = [r[0] for r in rows]
opn = np.array([r[1] for r in rows], dtype=float)
high = np.array([r[2] for r in rows], dtype=float)
low = np.array([r[3] for r in rows], dtype=float)
close = np.array([r[4] for r in rows], dtype=float)
yb = np.array([r[5] for r in rows], dtype=float)
fb = np.array([r[7] for r in rows], dtype=float)
fs = np.array([r[8] for r in rows], dtype=float)
toi = np.array([r[9] for r in rows], dtype=float)
toi = np.where(toi <= 0, 1, toi)

dyb = np.diff(yb)
fiz_net = (fb - fs) / toi * 100
dfiz = np.diff(fiz_net)

n = len(rows)
nf = 5
fsize = n // nf

results = []

for hold in [1, 2, 3, 5, 7, 10]:
    for sl in [0.0, 0.01, 0.02, 0.03, 0.05]:
        for mode in [0, 1, 2, 3]:
            fold_rets = []
            fold_dds = []
            
            for f in range(nf):
                s = f * fsize
                e = n if f == 4 else (f + 1) * fsize
                eq = CAPITAL
                peak = eq
                mdd = 0
                
                for i in range(s, e - 1):
                    sig = False
                    if mode == 0 and dyb[i] > 0:
                        sig = True
                    elif mode == 1 and dyb[i] > 0 and dfiz[i] < 0:
                        sig = True
                    elif mode == 2:
                        pos_dyb = dyb[dyb > 0]
                        med = np.median(pos_dyb) if len(pos_dyb) > 0 else 0
                        if dyb[i] > med:
                            sig = True
                    elif mode == 3 and dyb[i] > 0 and i >= 2 and dyb[i-2] < 0 and dyb[i-1] < 0:
                        sig = True
                    
                    if not sig:
                        continue
                    
                    ei = i + 1
                    xi = min(ei + hold, n - 1)
                    if ei >= n - 1:
                        continue
                    
                    ep = float(opn[ei])
                    stop_hit = False
                    xp = float(close[xi])
                    
                    if sl > 0:
                        sp = ep * (1 - sl)
                        for j in range(ei, xi + 1):
                            if float(low[j]) <= sp:
                                xp = sp
                                stop_hit = True
                                break
                    
                    go = ep * CS
                    nc = max(1, int(eq // go)) if go > 0 else 1
                    gp = nc * CS * (xp - ep)
                    cm = nc * COMM
                    eq += gp - cm
                    if eq > peak:
                        peak = eq
                    dd = (peak - eq) / peak * 100
                    mdd = max(mdd, dd)
                
                ret = (eq - CAPITAL) / CAPITAL * 100
                fold_rets.append(ret)
                fold_dds.append(mdd)
            
            mr = np.mean(fold_rets)
            md = np.mean(fold_dds)
            mnr = min(fold_rets)
            mxd = max(fold_dds)
            nneg = sum(1 for r in fold_rets if r < 0)
            cal = mr / md if md > 0 else 0
            score = cal * (1 - nneg / nf * 0.5)
            
            if mr > 5 and md < 15:
                mode_s = ['yb_up','smart_money','yb_strong','yb_reversal'][mode]
                print(f'{mode_s:>12} hold={hold:>2} sl={sl:.0%} | ret={mr:+6.1f}% dd={md:4.1f}% min={mnr:+6.1f}% maxdd={mxd:4.1f}% score={score:.2f}')
                results.append({
                    'mode': mode_s, 'hold': hold, 'sl': sl,
                    'mean_ret': round(mr,2), 'mean_dd': round(md,2),
                    'min_ret': round(mnr,2), 'max_dd': round(mxd,2),
                    'score': round(score,2)
                })

print()
print('=== GD TOP ===')
results.sort(key=lambda x: -x['score'])
for r in results[:20]:
    print(f'{r["mode"]:>12} hold={r["hold"]:>2} sl={r["sl"]:.0%} | ret={r["mean_ret"]:+6.1f}% dd={r["mean_dd"]:4.1f}% min={r["min_ret"]:+6.1f}% maxdd={r["max_dd"]:4.1f}% score={r["score"]:.2f}')

with open('reports/oi_daily_gd_grid.json','w') as f:
    json.dump(results, f, indent=2)
print(f'\nSaved ({len(results)} combos)')
