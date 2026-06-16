#!/usr/bin/env python3
"""GD Walk-forward: PCT_v95_yb90 signal, 4 folds, honest MTM."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
CONTRACT_SIZE = 10
COMM_RT = 4
TICKER = 'GD'

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

rows = ch.query('''
    SELECT p.time, p.open, p.high, p.low, p.close, p.volume, 
           o.yur_buy, o.yur_sell, o.total_oi
    FROM moex.prices_5m p
    INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
    WHERE p.symbol = %(t)s AND p.time >= '2025-01-01' AND p.time <= '2026-05-01'
    ORDER BY p.time
''', parameters={'t': TICKER}).result_rows

df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume','yur_buy','yur_sell','total_oi'])
print(f'{TICKER}: {len(df)} bars')

# 4 folds
folds = [
    ('Fold1_2025H1', '2025-01-01', '2025-06-30'),
    ('Fold2_2025H2', '2025-07-01', '2025-12-31'),
    ('Fold3_2026Q1', '2026-01-01', '2026-03-31'),
    ('Fold4_2026Q2', '2026-04-01', '2026-05-01'),
]

all_results = {}

for fname, fstart, fend in folds:
    mask = (df['time'] >= fstart) & (df['time'] <= fend)
    sub = df[mask].copy().reset_index(drop=True)
    if len(sub) < 20:
        print(f'  {fname}: {len(sub)} bars — skip')
        all_results[fname] = None
        continue
    
    # Rolling percentiles on sub only
    vol_pct = sub['volume'].rolling(20, min_periods=10).rank(pct=True)
    yb_pct = sub['yur_buy'].rolling(20, min_periods=10).rank(pct=True)
    signal = (vol_pct >= 0.95) & (yb_pct >= 0.90)
    
    # Entries
    entries = []
    for i in range(1, len(sub)):
        if signal.iloc[i-1]:
            ep = float(sub['open'].iloc[i])
            if ep <= 0:
                continue
            go = ep * CONTRACT_SIZE
            n_con = max(1, int(CAPITAL // go)) if go > 0 else 1
            entries.append({'idx': i, 'ep': ep, 'n': n_con, 'go': go})
    
    # Test BOTH holds
    for hold in [40, 80]:
        equity = CAPITAL
        eq_curve = [equity]
        peak = equity
        trades = []
        max_dd = 0
        
        for e in entries:
            ei = e['idx']
            xi = min(ei + hold, len(sub) - 1)
            if ei >= len(sub) - 1:
                continue
            
            ep = e['ep']
            nc = e['n']
            sp = ep * 0.98
            hit_stop = False
            xp = float(sub['close'].iloc[xi])
            
            for j in range(ei, xi + 1):
                if float(sub['low'].iloc[j]) <= sp:
                    xp = sp
                    hit_stop = True
                    break
            
            gp = nc * CONTRACT_SIZE * (xp - ep)
            cm = nc * COMM_RT
            npnl = gp - cm
            equity += npnl
            eq_curve.append(equity)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
            
            trades.append({
                'entry': str(sub['time'].iloc[ei])[:19],
                'exit': str(sub['time'].iloc[xi])[:19],
                'ep': float(ep), 'xp': float(xp),
                'gp': round(gp, 2), 'cm': round(cm, 2), 'npnl': round(npnl, 2),
                'n': nc, 'stop': hit_stop
            })
        
        ret = (equity - CAPITAL) / CAPITAL * 100
        calmar = ret / max_dd if max_dd > 0 else 0
        wins = sum(1 for t in trades if t['npnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        gross = sum(t['gp'] for t in trades)
        comm = sum(t['cm'] for t in trades)
        net = sum(t['npnl'] for t in trades)
        pf = abs(sum(t['npnl'] for t in trades if t['npnl'] > 0) / (sum(abs(t['npnl']) for t in trades if t['npnl'] < 0) + 1))
        
        label = f'{fname}_h{hold}'
        all_results[label] = {
            'trades': len(trades), 'ret_pct': round(ret, 2),
            'max_dd_pct': round(max_dd, 2), 'calmar': round(calmar, 2),
            'wr_pct': round(wr, 1), 'pf': round(pf, 2),
            'comm': round(comm, 2), 'net_pnl': round(net, 2)
        }
        print(f'  {label:>20}: tr={len(trades):>4} ret={ret:>+7.2f}% DD={max_dd:>5.2f}% Calmar={calmar:>6.2f} WR={wr:>4.1f}% PF={pf:>4.2f} comm={comm:>8.0f}')

# Summary
print(f'\n=== {TICKER} Walk-Forward Summary ===')
print(f'{"Fold":>20} {"Hold":>4} {"Tr":>4} {"Ret%":>8} {"DD%":>7} {"Calmar":>7} {"WR%":>5} {"PF":>5} {"Comm":>8}')
print('-' * 75)
for k in sorted(all_results.keys()):
    r = all_results[k]
    if r:
        print(f'{k:>20} {r["trades"]:>4} {r["ret_pct"]:>+8.2f} {r["max_dd_pct"]:>7.2f} {r["calmar"]:>7.2f} {r["wr_pct"]:>5.1f} {r["pf"]:>5.2f} {r["comm"]:>8.0f}')

with open('reports/oi_volume_backtest/gd_walkforward.json', 'w') as f:
    json.dump(all_results, f, indent=2, default=str)
print('\nSaved to reports/oi_volume_backtest/gd_walkforward.json')
