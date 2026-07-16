#!/usr/bin/env python3
"""Optimize: max CAGR while MTM DD ≤ 20%. Tests caps, risk, tickers."""
import clickhouse_connect as cc, numpy as np, psycopg2
from collections import defaultdict
from strategies.stop_hunt.prod.engine import check_signal as sh_check
import sys

ch = cc.get_client(host='10.0.0.64', port=8123)
P = [('GAZR','GZ'),('Si','Si'),('ROSN','RN'),('GOLD','GD')]
pg = psycopg2.connect(host='10.0.0.64',port=5432,dbname='moex',user='postgres',password='')
cur=pg.cursor(); spe={}
for _,t in P:
    cur.execute('SELECT step_price,min_step,lot_volume,go,pct FROM futures.ticker_specs WHERE ticker=%s',(t,))
    r=cur.fetchone()
    if r: spe[t]={'sp':float(r[0]or 1),'ms':float(r[1]or 0.01),'lot':int(r[2]or 1),'go':float(r[3]or 0),'pct':float(r[4]or 1.0)}
cur.close();pg.close()

data={}
for asset,tkr in P:
    q="SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,argMax(pr_open,SYSTIME) as opn,argMax(pr_high,SYSTIME) as hi,argMax(pr_low,SYSTIME) as lo,argMax(pr_close,SYSTIME) as prc FROM moex.tradestats_fo WHERE asset_code='%s' AND SYSTIME>='2025-01-01' GROUP BY bt ORDER BY bt"%asset
    df=ch.query_df(q)
    if df.empty or len(df)<1000:continue
    data[tkr]=df
ml=max(len(df)for df in data.values());TC=4;TO=12

results=[]
for TICKERS in [P, [p for p in P if p[1]!='GZ']]:  # all, without GZ
    for RISK in [0.01, 0.015, 0.02]:
        for MAX_CTR in [5, 10, 20, 50]:
            for GO_D in [0.6, 1.0]:
                at=[];po=[];CAP=200000.0;eq=CAP;peak=CAP;dd_pct=0;mtm_peak=CAP;mtm_dd=0
                label = 'ALL' if len(TICKERS)==4 else 'NO_GZ'
                for bi in range(50,ml):
                    for tkr,df in data.items():
                        if label=='NO_GZ' and tkr=='GZ': continue
                        if bi>=len(df):continue
                        if any(not p['cls']and p['tk']==tkr for p in po):continue
                        go=spe[tkr]['go']*GO_D
                        if go<=0:continue
                        cc=min(MAX_CTR, max(1,int(eq*RISK/go)))
                        ms=spe[tkr]['ms']
                        bd={'prc':float(df['prc'].iloc[bi]),'hi':float(df['hi'].iloc[bi]),'lo':float(df['lo'].iloc[bi])}
                        if bi>=20:bd['lo_hist']=list(df['lo'].iloc[bi-20:bi].values);bd['hi_hist']=list(df['hi'].iloc[bi-20:bi].values)
                        sig=sh_check(bd,tkr)
                        if not sig:continue
                        ni=bi+1
                        if ni>=len(df):continue
                        ep=float(df['opn'].iloc[ni])+ms;ep=round(ep/ms)*ms
                        po.append({'tk':tkr,'eb':ni,'ep':ep,'cls':False,'pnl':0,'tp':None,'act':False,'ebi':bi,'c':cc,'lt':spe[tkr]['lot'],'pct':spe[tkr]['pct']})
                    for p in po:
                        if p['cls']:continue
                        tkr=p['tk'];df=data[tkr];cc2=p['c'];lt=p['lt'];pct=p['pct']
                        if bi>=len(df)or p['eb']>=bi:continue
                        hi,lo=float(df['hi'].iloc[bi]),float(df['lo'].iloc[bi]);close=float(df['prc'].iloc[bi])
                        s=spe[tkr];sp,ms=s['sp'],s['ms']
                        if bi-p['ebi']>=TO:
                            p['pnl']=(close-p['ep'])/ms*sp*lt*pct*cc2-TC*cc2;p['cls']=True;at.append(p);eq+=p['pnl']
                            if eq>peak:peak=eq
                            else:dd_pct=max(dd_pct,(peak-eq)/peak*100)
                            continue
                        if not p['act']:
                            if hi>=p['ep']*1.005:p['act']=True;p['tp']=hi*(1-0.003)
                        elif hi>=p['tp']/(1-0.003):p['tp']=hi*(1-0.003)
                        ex=None
                        if p['act']and lo<=p['tp']:ex=p['tp']
                        elif lo<=p['ep']*0.993:ex=lo
                        if not ex:continue
                        p['pnl']=(ex-p['ep'])/ms*sp*lt*pct*cc2-TC*cc2;p['cls']=True;at.append(p);eq+=p['pnl']
                        if eq>peak:peak=eq
                        else:dd_pct=max(dd_pct,(peak-eq)/peak*100)
                if not at:continue
                pnls=np.array([t['pnl']for t in at]);wins=pnls[pnls>0];losses=pnls[pnls<=0]
                wr=len(wins)/len(pnls)*100;pf=abs(sum(wins)/sum(losses))if len(losses)>0and sum(losses)!=0else 999
                cagr=((eq/CAP)**(1/1.5)-1)*100 if eq>0 else -100
                key = f'C={label} R={RISK*100:.0f}% MAX={MAX_CTR} GO={GO_D*100:.0f}%'
                status='OK' if dd_pct<=12 and eq>CAP else 'DD' if dd_pct>12 else 'LOSS'
                print(f'{key:45s} CAGR={cagr:>8.0f}% DD={dd_pct:>5.1f}% EQ={eq/1000:>8.0f}K {status}', flush=True)
                results.append((cagr, dd_pct, eq, key))

print('\n=== BEST (CAGR with DD≤20%) ===')
results.sort(key=lambda x:-x[0])
for cagr, dd, eq, key in results[:10]:
    if dd<=12 and eq>200000:
        print(f'{key:45s} CAGR={cagr:>8.0f}% DD={dd:>5.1f}% EQ={eq/1000:>8.0f}K')
