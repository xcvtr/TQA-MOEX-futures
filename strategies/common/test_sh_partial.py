#!/usr/bin/env python3
"""Test partial exit for Stop Hunt: close 50% at TP, let 50% trail."""
import clickhouse_connect as cc, numpy as np
from strategies.stop_hunt.prod.engine import check_signal as sh_check
import sys

ch = cc.get_client(host='10.0.0.64', port=8123, database='moex')
P = [('GAZR','GZ'),('SBRF','SR'),('NG','NG'),('VTBR','VB'),('WHEAT','W4'),('Si','Si')]
SPE={'GZ':{'sp':1,'ms':1},'SR':{'sp':1,'ms':1},'NG':{'sp':7.7,'ms':0.01},'VB':{'sp':1,'ms':1},'W4':{'sp':10,'ms':1},'Si':{'sp':1,'ms':1}}
TO=12; TC=4

data={}
for asset,tkr in P:
    df=ch.query_df(f"SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,argMax(pr_open,SYSTIME) as opn,argMax(pr_high,SYSTIME) as hi,argMax(pr_low,SYSTIME) as lo,argMax(pr_close,SYSTIME) as prc FROM moex.tradestats_fo WHERE asset_code='{asset}' AND SYSTIME>='2025-01-01' GROUP BY bt ORDER BY bt")
    if df.empty or len(df)<1000: continue
    data[tkr]=df
ml=max(len(df) for df in data.values())

configs=[('Baseline trail',{'partial':False}),('Partial 50%@1%',{'partial':True,'tp_pct':0.01,'part_pct':0.5,'trail_act':0.005,'trail_drop':0.003})]

for cname,cparams in configs:
    at=[]; po=[]
    for bi in range(50,ml):
        for tkr,df in data.items():
            if bi>=len(df): continue
            if any(not p['cls'] and p['tk']==tkr for p in po): continue
            ms=SPE.get(tkr,{'ms':0.01})['ms']
            bd={'prc':float(df['prc'].iloc[bi]),'hi':float(df['hi'].iloc[bi]),'lo':float(df['lo'].iloc[bi]),'opn':float(df['opn'].iloc[bi]),'vol':1.0,'dcvd_z':0}
            if bi>=20: bd['lo_hist']=list(df['lo'].iloc[bi-20:bi].values); bd['hi_hist']=list(df['hi'].iloc[bi-20:bi].values)
            sig=sh_check(bd,tkr)
            if sig:
                ni=bi+1
                if ni<len(df):
                    ms_val=SPE.get(tkr,{'ms':0.01})['ms']
                    ep=float(df['opn'].iloc[ni])+ms_val; ep=round(ep/ms_val)*ms_val
                    full_c={'size':1}  # 1 contract
                    po.append({'tk':tkr,'eb':ni,'ep':ep,'part':None,'part_pos':None,'full':full_c,'cls':False,'pnl':0,'tp':None,'act':False,'ebi':bi,'dir':sig['direction'],'partial':cparams.get('partial',False),'tp_pct':cparams.get('tp_pct',0),'part_pct':cparams.get('part_pct',0),'trail_act':cparams.get('trail_act',0.005),'trail_drop':cparams.get('trail_drop',0.003)})
        for p in po:
            if p['cls']: continue
            tkr=p['tk']; df=data[tkr]
            if bi>=len(df) or p['eb']>=bi: continue
            hi,lo=float(df['hi'].iloc[bi]),float(df['lo'].iloc[bi])
            s=SPE.get(tkr,{'sp':1,'ms':0.01}); sp=s['sp']; ms=s['ms']
            if bi-p['ebi']>=TO:
                prc_exit=float(df['prc'].iloc[bi])
                p['pnl']=(prc_exit-p['ep'])/ms*sp*p['full']['size']-TC
                p['cls']=True
                if p['part_pos'] is not None: p['pnl']+=p['part_pos']['pnl']
                at.append(p)
                continue
            # Handle partial exit: close 50% at tp_pct
            if p['partial'] and p['part'] is None:
                if p['dir']!='short':
                    if hi>=p['ep']*(1+p['tp_pct']):
                        tp_price=p['ep']*(1+p['tp_pct'])
                        pnl=((tp_price-p['ep'])/ms)*sp*(p['full']['size']*p['part_pct'])-TC
                        p['part']={'price':tp_price,'pnl':pnl}
                        p['part_pos']={'pnl':pnl}
                        p['full']['size']=int(p['full']['size']*(1-p['part_pct']))
            # Trailing TP on remaining position
            if not p['act']:
                if hi>=p['ep']*(1+p['trail_act']): p['act']=True; p['tp']=hi*(1-p['trail_drop'])
            elif hi>=p['tp']/(1-p['trail_drop']): p['tp']=hi*(1-p['trail_drop'])
            ex=None
            if p['act'] and lo<=p['tp']: ex=p['tp']
            elif lo<=p['ep']*0.993: ex=lo
            if ex:
                pnl=(ex-p['ep'])/ms*sp*p['full']['size']-TC
                p['pnl']=pnl+(p['part_pos']['pnl'] if p['part_pos'] else 0)
                p['cls']=True; at.append(p)
    
    pnls=np.array([t['pnl'] for t in at]); wins=pnls[pnls>0]; losses=pnls[pnls<=0]
    wr=len(wins)/len(pnls)*100 if len(pnls)>0 else 0
    pf=abs(sum(wins)/sum(losses)) if len(losses)>0 and sum(losses)!=0 else 999
    pt_d={}
    for t in at: pt_d.setdefault(t['tk'],[]).append(t['pnl'])
    pl=' '.join(f"{k}:{sum(v)/1000:.0f}K" for k,v in sorted(pt_d.items()))
    part_info=f" part={cparams['part_pct']*100:.0f}%@{cparams.get('tp_pct',0)*100:.0f}%" if cparams.get('partial',False) else ''
    print(f'{cname:25s} | Trades={len(at):>5} | PnL={sum(pnls)/1000:>+7.0f}K | WR={wr:.1f}% | PF={pf:.2f} | {pl}{part_info}',flush=True)
