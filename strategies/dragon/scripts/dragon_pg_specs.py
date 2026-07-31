#!/usr/bin/env python3 -u
"""Dragon MT5 — correct GO from PG, GO/2, KNUR."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.dragon.prod.engine import check_signal as dragon_check

# REAL specs from PG futures.ticker_specs (FULL GO)
SPECS = {
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 1475, 'lot': 1},   # FINAM MOEX
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 949, 'lot': 100},    # FINAM MOEX
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 20519.04, 'lot': 100},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 34328.0, 'lot': 10},
    'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 30706.7, 'lot': 10},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 3643.44, 'lot': 1000},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 4054, 'lot': 100},   # FINAM MOEX
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 6662, 'lot': 1000},  # FINAM MOEX
    'GD': {'ms': 0.1, 'sp': 7.84756, 'go': 16607, 'lot': 1}, # FINAM MOEX
}
ALL_TICKERS = ['MM', 'GZ']

TA, TT, SL, TO = 0.015, 0.005, 0.01, 60  # dragon params

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_bars = {}
for t in ALL_TICKERS:
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc FROM moex.mt5_continuous WHERE ticker='{t}' AND bt>='2025-07-16' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts=r[0]; h,m=ts.hour,ts.minute
        if ts.weekday()>=5: continue
        if h<15 or h>23 or (h==23 and m>45): continue
        bars.append({'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4])})
    if len(bars) > 5000:
        all_bars[t]=bars
        print(f'{t}: {len(bars)} M1 bars', flush=True)
ch.close()

n = min(len(all_bars[t]) for t in all_bars)
print(f'Min bars: {n}', flush=True)

for risk_pct in [1, 2, 3, 4, 5, 7, 10]:
    risk = risk_pct / 100.0
    KNUR = 0.5
    equity=200000.0; peak=mtm_peak=equity; cash_mdd=mtm_mdd=0
    positions={}; trades=[]; m5_cache={t:[] for t in all_bars}
    
    for i in range(30, n):
        if i%5==4:
            for t in all_bars:
                g=all_bars[t][i-5:i]
                if len(g)>=3:
                    m5_cache[t].append({'opn':g[0]['opn'],'hi':max(b['hi']for b in g),'lo':min(b['lo']for b in g),'prc':g[-1]['prc']})
        floating=0.0
        for t in list(positions.keys()):
            p=positions[t]; b=all_bars[t][i]; ms,sp=SPECS[t]['ms'],SPECS[t]['sp']
            ex=None; slev=p['ep']*(1-SL) if p['dir']=='long' else p['ep']*(1+SL)
            if (p['dir']=='long' and b['lo']<=slev) or (p['dir']=='short' and b['hi']>=slev): ex=slev
            if not ex and i%5==4:
                if not p.get('tr'):
                    if (p['dir']=='long' and b['hi']>=p['ep']*(1+TA)) or (p['dir']=='short' and b['lo']<=p['ep']*(1-TA)):
                        p['tr']=True; p['tl']=b['hi']*(1-TT) if p['dir']=='long' else b['lo']*(1+TT)
                if p.get('tr') and ((p['dir']=='long' and b['lo']<=p['tl']) or (p['dir']=='short' and b['hi']>=p['tl'])): ex=p['tl']
            if not ex and i-p['bi']>=TO: ex=b['prc']
            if ex:
                pnl=(ex-p['ep'])/ms*sp*(1 if p['dir']=='long' else -1)*p['shares']-4*p['shares']
                equity+=pnl; trades.append(pnl); peak=max(peak,equity); cash_mdd=max(cash_mdd,(peak-equity)/peak*100)
                del positions[t]
            else: floating+=(b['prc']-p['ep'])/ms*sp*(1 if p['dir']=='long' else -1)*p['shares']
        mtm_val=equity+floating; mtm_peak=max(mtm_peak,mtm_val); mtm_mdd=max(mtm_mdd,(mtm_peak-mtm_val)/mtm_peak*100) if mtm_peak>0 else 0
        if i%5==4:
            for t in all_bars:
                if t in positions or not m5_cache[t] or len(m5_cache[t])<30: continue
                sig=dragon_check({'bars_list':m5_cache[t],'prc':m5_cache[t][-1]['prc']},t,{'impulse_pct':0.3,'retrace_max_pct':70,'hump_extension':0.1,'lookback':100})
                if sig:
                    # Trend filter: SMA 50 — buy above, sell below
                    if len(m5_cache[t]) >= 50:
                        sma50 = sum(b['prc'] for b in m5_cache[t][-50:]) / 50
                        current_prc = m5_cache[t][-1]['prc']
                        if sig['direction'] == 'long' and current_prc < sma50:
                            continue  # no long in downtrend
                        if sig['direction'] == 'short' and current_prc > sma50:
                            continue  # no short in uptrend
                    half_go = SPECS[t]['go']  # already FINAM-reduced
                    shares = max(1, int(equity * risk / half_go))
                    go_used = sum((SPECS[p['ticker']]['go']/2)*p['shares'] for p in positions.values())
                    if go_used + half_go * shares <= equity * KNUR:
                        positions[t]={'ticker':t,'dir':sig['direction'],'ep':sig['entry_price'],'bi':i,'shares':shares,'tr':False}
    n_tr=len(trades)
    if n_tr:
        wins=[p for p in trades if p>0]; losses=[p for p in trades if p<=0]
        wr=len(wins)/n_tr*100; pf=sum(wins)/sum(abs(p)for p in losses) if losses else 0
        ret=(equity-200000)/200000*100
    else: wr=pf=ret=0
    print(f'risk={risk_pct:2d}% ret={ret:>+6.1f}% pf={pf:.2f} mtmMDD={mtm_mdd:.2f}% tr={n_tr}')
