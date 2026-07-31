#!/usr/bin/env python3 -u
"""Dragon on MT5 FINAM — risk-scaled like mtm_backtest.py"""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc, pandas as pd
from strategies.dragon.prod.engine import check_signal as dragon_check

SPECS = {
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'NG': {'ms': 0.001, 'sp': 7.79568, 'go': 6406.22},
    'BR': {'ms': 0.01, 'sp': 7.79568, 'go': 13977.67},
    'SV': {'ms': 0.01, 'sp': 7.79568, 'go': 10022.27},
}
PRIORITY = ['NG','SV','BR','MM','GZ']

TA, TT, SL, TIMEOUT = 0.015, 0.005, 0.01, 60
RISK = 0.12  # 12% of equity
KNUR = 0.5

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_bars = {}
for t in PRIORITY:
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc FROM moex.mt5_continuous
        WHERE ticker='{t}' AND bt>='2025-07-16' ORDER BY bt
    """).result_rows
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts':ts,'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4])})
    all_bars[t] = bars
    print(f'{t}: {len(bars)} M1 bars', flush=True)
ch.close()

n = min(len(all_bars[t]) for t in PRIORITY)
print(f'Min bars: {n}', flush=True)

equity = 200000.0
peak, mtm_peak, cash_mdd, mtm_mdd = equity, equity, 0, 0
positions, trades = {}, []
m5_cache = {t: [] for t in PRIORITY}

for i in range(30, n):
    # Update M5
    if i % 5 == 4:
        for t in PRIORITY:
            g = all_bars[t][i-5:i]
            if len(g) >= 3:
                m5_cache[t].append({'opn':g[0]['opn'],'hi':max(b['hi'] for b in g),
                    'lo':min(b['lo'] for b in g),'prc':g[-1]['prc']})
    
    # Manage positions
    floating = 0.0
    for t in list(positions.keys()):
        pos = positions[t]; bar = all_bars[t][i]
        ep, ms, sp = pos['ep'], SPECS[t]['ms'], SPECS[t]['sp']
        ex = None
        slev = ep*(1-SL) if pos['dir']=='long' else ep*(1+SL)
        if (pos['dir']=='long' and bar['lo']<=slev) or (pos['dir']=='short' and bar['hi']>=slev): ex=slev
        if not ex and i%5==4:
            if not pos.get('tr'):
                if (pos['dir']=='long' and bar['hi']>=ep*(1+TA)) or (pos['dir']=='short' and bar['lo']<=ep*(1-TA)):
                    pos['tr']=True; pos['tl']=bar['hi']*(1-TT) if pos['dir']=='long' else bar['lo']*(1+TT)
            if pos.get('tr'):
                if (pos['dir']=='long' and bar['lo']<=pos['tl']) or (pos['dir']=='short' and bar['hi']>=pos['tl']): ex=pos['tl']
        if not ex and i-pos['bi']>=TIMEOUT: ex=bar['prc']
        if ex is not None:
            gross = (ex-ep)/ms*sp * (1 if pos['dir']=='long' else -1) * pos['shares']
            pnl = gross - 4 * pos['shares']
            equity += pnl; trades.append(pnl)
            peak = max(peak, equity)
            cash_mdd = max(cash_mdd, (peak-equity)/peak*100)
            del positions[t]
        else:
            mtm = (bar['prc']-ep)/ms*sp*(1 if pos['dir']=='long' else -1)*pos['shares'] - 4*pos['shares']
            floating += mtm
    
    mtm_val = equity + floating
    mtm_peak = max(mtm_peak, mtm_val)
    mtm_mdd = max(mtm_mdd, (mtm_peak-mtm_val)/mtm_peak*100) if mtm_peak>0 else 0
    
    # Check signals
    if i % 5 == 4:
        go_limit = equity * KNUR
        for t in PRIORITY:
            if t in positions or not m5_cache[t] or len(m5_cache[t]) < 30: continue
            sig = dragon_check({'bars_list': m5_cache[t], 'prc': m5_cache[t][-1]['prc']}, t,
                {'impulse_pct':0.3,'retrace_max_pct':70,'hump_extension':0.1,'lookback':100})
            if sig:
                go = SPECS[t]['go'] * 2  # GO×0.5 → полный ГО
                shares = max(1, int(equity * RISK / go))
                if sum(SPECS[p['ticker']]['go']*2*p.get('shares',1) for p in positions.values()) + go*shares <= go_limit:
                    positions[t] = {'ticker':t,'dir':sig['direction'],'ep':sig['entry_price'],
                                    'bi':i,'shares':shares,'tr':False}

n = len(trades)
if n:
    wins=[p for p in trades if p>0]; losses=[p for p in trades if p<=0]
    wr=len(wins)/n*100; pf=sum(wins)/sum(abs(p) for p in losses) if losses else 0
    ret=(equity-200000)/200000*100
else: wr=pf=ret=0
print(f'\nResult: tr={n} wr={wr:.1f}% pf={pf:.2f} ret={ret:+.1f}% cashMDD={cash_mdd:.2f}% mtmMDD={mtm_mdd:.2f}%')
