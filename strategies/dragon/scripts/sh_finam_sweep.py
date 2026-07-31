#!/usr/bin/env python3 -u
"""SH RN sweep with REAL FINAM GO."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

SPEC = {'ms': 1.0, 'sp': 1.0, 'go': 4054, 'lot': 100}  # FINAM RN
TA, TT, SL, TO = 0.005, 0.003, 0.007, 12  # SH params from sweep (1-min detect)

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc FROM moex.mt5_continuous WHERE ticker='RN' AND bt>='2025-07-16' ORDER BY bt").result_rows
bars = []
for r in rows:
    ts=r[0]; h,m=ts.hour,ts.minute
    if ts.weekday()>=5: continue
    if h<15 or h>23 or (h==23 and m>45): continue
    bars.append({'ts':ts,'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4])})
print(f'RN: {len(bars)} M1 bars', flush=True)

print('risk%  ret%     cashMDD mtmMDD  PF     WR    Trades')
for risk_pct in [1, 2, 3, 4, 5, 7]:
    risk = risk_pct / 100.0
    equity=200000.0; peak=equity; mtm_peak=equity; cash_mdd=mtm_mdd=0
    pos=None; trades=[]; 
    ms,sp,go=SPEC['ms'],SPEC['sp'],SPEC['go']

    for i in range(30, len(bars)):
        b=bars[i]
        # Tick
        if pos:
            ex=None
            slev=pos['ep']*(1-SL) if pos['dir']=='long' else pos['ep']*(1+SL)
            if (pos['dir']=='long' and b['lo']<=slev) or (pos['dir']=='short' and b['hi']>=slev): ex=slev
            if not ex:
                if not pos.get('tr'):
                    if (pos['dir']=='long' and b['hi']>=pos['ep']*(1+TA)) or (pos['dir']=='short' and b['lo']<=pos['ep']*(1-TA)):
                        pos['tr']=True; pos['tl']=b['hi']*(1-TT) if pos['dir']=='long' else b['lo']*(1+TT)
                if pos.get('tr') and ((pos['dir']=='long' and b['lo']<=pos['tl']) or (pos['dir']=='short' and b['hi']>=pos['tl'])): ex=pos['tl']
            if not ex and i-pos['bi']>=TO: ex=b['prc']
            if ex:
                pnl=(ex-pos['ep'])/ms*sp*(1 if pos['dir']=='long' else -1)*pos['shares']-4*pos['shares']
                equity+=pnl; trades.append(pnl); peak=max(peak,equity); cash_mdd=max(cash_mdd,(peak-equity)/peak*100)
                pos=None
        floating=(b['prc']-pos['ep'])/ms*sp*(1 if pos['dir']=='long' else -1)*pos['shares'] if pos else 0
        mtm_val=equity+floating; mtm_peak=max(mtm_peak,mtm_val); mtm_mdd=max(mtm_mdd,(mtm_peak-mtm_val)/mtm_peak*100) if mtm_peak>0 else 0

        # Detect — every bar (1-min detect)
        if not pos:
            bd={
                'prc':b['prc'],'hi':b['hi'],'lo':b['lo'],
                'lo_hist':[x['lo'] for x in bars[max(0,i-80):i]],
                'hi_hist':[x['hi'] for x in bars[max(0,i-80):i]],
            }
            sig=sh_check(bd,'RN',{'lookback':60,'retrace':0.05})
            if sig:
                shares=max(1,int(equity*risk/go))
                pos={'dir':sig['direction'],'ep':sig['entry_price'],'bi':i,'shares':shares,'tr':False}

    n_tr=len(trades)
    if n_tr:
        wins=[p for p in trades if p>0]; losses=[p for p in trades if p<=0]
        wr=len(wins)/n_tr*100; pf=sum(wins)/sum(abs(p)for p in losses) if losses else 0
        ret=(equity-200000)/200000*100
        mark=' ✅' if mtm_mdd<=20 else ''
        print(f'{risk_pct:3d}%  {ret:>+8.1f}%  {cash_mdd:>6.2f}%  {mtm_mdd:>6.2f}%  {pf:>5.2f}  {wr:5.1f}%  {n_tr:4d}{mark}')
    else:
        print(f'{risk_pct:3d}%  NO TRADES')
