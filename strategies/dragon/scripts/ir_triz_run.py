#!/usr/bin/env python3 -u
"""IR Si — TRIZ trend filter (5-min detect, 2 years)."""
import sys, os, warnings; warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc; import numpy as np
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc FROM moex.mt5_continuous WHERE ticker='Si' AND bt>='2025-07-16' ORDER BY bt").result_rows
ch.close()
bars = []
for r in rows:
    ts=r[0]; h,m=ts.hour,ts.minute
    if ts.weekday()>=5 or h<15 or h>23 or (h==23 and m>45): continue
    bars.append({'ts':ts,'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4])})
print(f'Si: {len(bars)} M1 bars', flush=True)

GO=6453; TA,TT,SL,TO=0.005,0.003,0.007,12; TC=4
par={'impulse_bars':12,'impulse_pct':0.3,'cooldown':12,'min_vol_pct':0}

def run(trend):
    reset_state()
    eq=200000.0; peak=eq; mtm_pk=eq; cdd=mdd=0; pos=None; trades=[]; last=-999
    for mi in range(60,len(bars)):
        b=bars[mi]
        if pos:
            ex=None
            if b['lo']<=pos['ep']*0.993: ex=pos['ep']*0.993
            elif not pos.get('tr') and b['hi']>=pos['ep']*1.005:
                pos['tr']=True; pos['tl']=b['hi']*0.997
            elif pos.get('tr') and b['lo']<=pos['tl']: ex=pos['tl']
            if not ex and mi-pos['bi']>=TO: ex=b['prc']
            if ex:
                pnl=(ex-pos['ep'])*pos['shares']-TC*pos['shares']
                eq+=pnl; trades.append(pnl); peak=max(peak,eq)
                cdd=max(cdd,(peak-eq)/peak*100); pos=None
        if not pos and mi%1==0:
            if trend and mi >= 50:
                sma50 = sum(bars[j]['prc'] for j in range(mi-50, mi)) / 50
                if b['prc'] < sma50: continue
            bd = {
                'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'], 'vol': 100,
                'bars_list': bars[:mi],
                'lo_hist': [bars[j]['lo'] for j in range(mi-20, mi)] if mi >= 20 else [bars[j]['lo'] for j in range(mi)],
                'hi_hist': [bars[j]['hi'] for j in range(mi-20, mi)] if mi >= 20 else [bars[j]['hi'] for j in range(mi)],
                'close_hist': [bars[j]['prc'] for j in range(mi-20, mi)] if mi >= 20 else [bars[j]['prc'] for j in range(mi)],
                'vol_hist': [100] * min(mi, 20),
            }
            sig=ir_check(bd,'Si',par)
            if sig:
                sh=max(1,int(eq*0.01/GO))
                pos={'dir':sig['direction'],'ep':sig['entry_price']+1,'bi':mi,'shares':sh,'tr':False}
        fl=(b['prc']-pos['ep'])*pos['shares'] if pos else 0
        mtm_pk=max(mtm_pk,eq+fl)
        if mtm_pk>0: mdd=max(mdd,(mtm_pk-eq-fl)/mtm_pk*100)
    n=len(trades)
    if n:
        w=[p for p in trades if p>0]; l=[p for p in trades if p<=0]
        wr=len(w)/n*100; pf=sum(w)/sum(abs(p)for p in l) if l else 0
        rt=(eq-200000)/200000*100
        return f'n={n:5d} WR={wr:5.1f}% PF={pf:>5.2f} ROI={rt:>+7.1f}% MDD={mdd:5.2f}%'
    return 'NO TRADES'

print('BASELINE:     ', run(False), flush=True)
print('TREND SMA50:  ', run(True), flush=True)
