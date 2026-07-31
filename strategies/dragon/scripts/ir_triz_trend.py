#!/usr/bin/env python3 -u
"""IR Si 1m — TRIZ filter comparison (5-min detect for speed)."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc FROM moex.mt5_continuous WHERE ticker='Si' AND bt>='2025-07-16' ORDER BY bt").result_rows
ch.close()
bars = []
for r in rows:
    ts=r[0]; h,m=ts.hour,ts.minute
    if ts.weekday()>=5: continue
    if h<15 or h>23 or (h==23 and m>45): continue
    bars.append({'ts':ts,'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4])})
print(f'{len(bars)} M1 bars', flush=True)

GO,MS,SP = 6453,1.0,1.0; TA,TT,SL,TO = 0.005,0.003,0.007,12; TC=4
par = {'impulse_bars':12,'impulse_pct':0.3,'cooldown':12,'min_vol_pct':0}

def run_test(trend=False):
    reset_state()
    eq=200000.0; peak=eq; mtm_pk=eq; cdd=mdd=0; pos=None; trades=[]
    for mi in range(60,len(bars)):
        b=bars[mi]
        if pos:
            ex=None
            if b['lo']<=pos['ep']*0.993: ex=pos['ep']*0.993
            elif not pos.get('tr') and b['hi']>=pos['ep']*1.005: pos['tr']=True; pos['tl']=b['hi']*0.997
            elif pos.get('tr') and b['lo']<=pos['tl']: ex=pos['tl']
            if not ex and mi-pos['bi']>=TO: ex=b['prc']
            if ex:
                pnl=(ex-pos['ep'])*pos['shares']-TC*pos['shares']
                eq+=pnl; trades.append(pnl); peak=max(peak,eq); cdd=max(cdd,(peak-eq)/peak*100); pos=None
        if not pos and mi%5==4:
            dh=bars[:mi]
            if trend and len(dh)>=50:
                sma50 = sum(x['prc'] for x in dh[-50:]) / 50
                if b['prc'] < sma50: continue
            bd={'prc':b['prc'],'hi':b['hi'],'lo':b['lo'],'vol':100,'bars_list':dh,
                'lo_hist':[x['lo'] for x in dh],'hi_hist':[x['hi'] for x in dh],
                'close_hist':[x['prc'] for x in dh],'vol_hist':[100]*len(dh)}
            sig=ir_check(bd,'Si',par)
            if sig:
                sh=max(1,int(eq*0.01/GO))
                slip=1; ep2=sig['entry_price']+slip
                pos={'dir':sig['direction'],'ep':ep2,'bi':mi,'shares':sh,'tr':False}
        fl=(b['prc']-pos['ep'])*pos['shares'] if pos else 0
        mv=eq+fl; mtm_pk=max(mtm_pk,mv); mdd=max(mdd,(mtm_pk-mv)/mtm_pk*100) if mtm_pk>0 else 0
    n=len(trades)
    if n:
        w=[p for p in trades if p>0]; l=[p for p in trades if p<=0]
        wr=len(w)/n*100; pf=sum(w)/sum(abs(p)for p in l) if l else 0; rt=(eq-200000)/200000*100
        return {'n':n,'wr':wr,'pf':pf,'ret':rt,'mdd':mdd}
    return {'n':0,'wr':0,'pf':0,'ret':0,'mdd':0}

print(f'\n{\"FILTER\":>20s}  {\"n\":>5s}  {\"WR%\":>6s}  {\"PF\":>5s}  {\"ROI%\":>8s}  {\"MDD%\":>6s}')
print('-'*58)
r = run_test(False)
print(f'{\"BASELINE\":>20s}  {r[\"n\"]:5d}  {r[\"wr\"]:5.1f}%  {r[\"pf\"]:>5.2f}  {r[\"ret\"]:>+7.1f}%  {r[\"mdd\"]:5.2f}%', flush=True)
r = run_test(True)
print(f'{\"TREND SMA50\":>20s}  {r[\"n\"]:5d}  {r[\"wr\"]:5.1f}%  {r[\"pf\"]:>5.2f}  {r[\"ret\"]:>+7.1f}%  {r[\"mdd\"]:5.2f}%', flush=True)
