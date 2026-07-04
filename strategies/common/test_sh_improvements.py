#!/usr/bin/env python3
"""Stop Hunt improvements: CVD-confirmed + per-symbol filters."""
import clickhouse_connect as cc, numpy as np, psycopg2
from collections import defaultdict
from strategies.stop_hunt.prod.engine import check_signal as sh_check

ch = cc.get_client(host='10.0.0.64', port=8123, database='moex')
TC=4; TO=12

# All tickers + per-symbol params
PORT = [
    ('GAZR','GZ',1,1.005,0.993),   # ticker, contracts, activation, stop
    ('SBRF','SR',1,1.005,0.993),
    ('NG','NG',1,1.005,0.993),
    ('VTBR','VB',1,1.005,0.993),
    ('WHEAT','W4',1,1.005,0.993),
    ('Si','Si',1,1.005,0.993),
]

pg = psycopg2.connect(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='')
cur = pg.cursor(); spe={}
for _,t,_,_,_ in PORT:
    cur.execute('SELECT go,step_price,min_step,lot_volume FROM futures.ticker_specs WHERE ticker=%s',(t,))
    r=cur.fetchone()
    if r: spe[t]={'sp':float(r[1] or 1),'ms':float(r[2] or 0.01),'lot':int(r[3] or 1)}
cur.close();pg.close()

def run(use_cvd, tickers_include, label):
    data={}
    for asset,tkr,ct,act,slp in PORT:
        if tkr not in tickers_include: continue
        df=ch.query_df(f"SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,argMax(pr_open,SYSTIME) as opn,argMax(pr_high,SYSTIME) as hi,argMax(pr_low,SYSTIME) as lo,argMax(pr_close,SYSTIME) as prc,sum(vol_b) as vb,sum(vol_s) as vs FROM moex.tradestats_fo WHERE asset_code='{asset}' AND SYSTIME>='2025-01-01' GROUP BY bt ORDER BY bt")
        if df.empty or len(df)<1000: continue
        n=len(df)
        # CVD z-score
        vb_=df['vb'].values.astype(float).clip(0); vs_=df['vs'].values.astype(float).clip(0)
        cvd_arr=vb_-vs_; dcvd=np.diff(cvd_arr,prepend=cvd_arr[0])
        dcvd_z=np.full(n,np.nan)
        for i in range(20,n):
            s=dcvd[i-20:i]
            if s.std()>0: dcvd_z[i]=(dcvd[i]-s.mean())/s.std()
        df['dcvd_z']=dcvd_z; df['vol']=np.maximum(vb_+vs_,1)
        data[tkr]=(df,ct,act,slp)

    ml=max(len(d[0]) for d in data.values()); all_t=[]; po=[]
    for bi in range(50,ml):
        for tkr,(df,ct,act,slp) in data.items():
            if bi>=len(df): continue
            if any(not p['cls'] and p['tk']==tkr for p in po): continue
            ms=spe.get(tkr,{'ms':0.01,'sp':1})['ms']
            prc=float(df['prc'].iloc[bi])
            bd={'prc':prc,'hi':float(df['hi'].iloc[bi]),'lo':float(df['lo'].iloc[bi]),'opn':float(df['opn'].iloc[bi]),'dcvd_z':float(df['dcvd_z'].iloc[bi]) if not np.isnan(df['dcvd_z'].iloc[bi]) else 0,'vol':float(df['vol'].iloc[bi])}
            if bi>=20: bd['lo_hist']=list(df['lo'].iloc[bi-20:bi].values); bd['hi_hist']=list(df['hi'].iloc[bi-20:bi].values)
            sig=sh_check(bd,tkr)
            if sig:
                dir=sig['direction']
                # CVD filter
                if use_cvd:
                    cz=bd['dcvd_z']
                    if dir=='long' and cz<0.3: continue
                    if dir=='short' and cz>-0.3: continue
                ni=bi+1
                if ni<len(df):
                    ep=float(df['opn'].iloc[ni])+ms; ep=round(ep/ms)*ms
                    po.append({'tk':tkr,'eb':ni,'ep':ep,'ct':ct,'cls':False,'pnl':0,'tp':None,'act':False,'ebi':bi,'act_lvl':act,'slp':slp})
        for p in po:
            if p['cls']: continue
            tkr=p['tk']; df=data[tkr][0]
            if bi>=len(df) or p['eb']>=bi: continue
            hi,lo=float(df['hi'].iloc[bi]),float(df['lo'].iloc[bi])
            s=spe.get(tkr,{'sp':1,'ms':0.01}); sp,ms=s['sp'],s['ms']
            if bi-p['ebi']>=TO:
                p['pnl']=(float(df['prc'].iloc[bi])-p['ep'])/ms*sp*p['ct']-TC*p['ct']; p['cls']=True; all_t.append(p); continue
            if not p['act']:
                if hi>=p['ep']*p['act_lvl']: p['act']=True; p['tp']=hi*(1-0.003)
            elif hi>=p['tp']/(1-0.003): p['tp']=hi*(1-0.003)
            ex=None
            if p['act'] and lo<=p['tp']: ex=p['tp']
            elif lo<=p['ep']*p['slp']: ex=lo
            if ex: p['pnl']=(ex-p['ep'])/ms*sp*p['ct']-TC*p['ct']; p['cls']=True; all_t.append(p)
    
    pnls=np.array([t['pnl'] for t in all_t])
    wins=pnls[pnls>0]; losses=pnls[pnls<=0]
    wr=len(wins)/len(pnls)*100 if len(pnls)>0 else 0
    pf=abs(sum(wins)/sum(losses)) if len(losses)>0 and sum(losses)!=0 else 999
    
    sy=defaultdict(lambda:{'p':[],'n':0})
    for t in all_t: sy[t['tk']]['p'].append(t['pnl']); sy[t['tk']]['n']+=1
    pl=' '.join(f"{k}:{sum(v['p'])/1000:.0f}K" for k,v in sorted(sy.items()))
    print(f'{label:25s} | Trades={len(all_t):>5} | PnL={sum(pnls)/1000:>+9.0f}K | WR={wr:.1f}% | PF={pf:.2f} | {pl}',flush=True)
    return len(all_t), sum(pnls), wr, pf

# Configs to test
configs = [
    (False, ['GZ','NG','Si','SR','VB','W4'], '1. SH baseline (all)'),
    (True,  ['GZ','NG','Si','SR','VB','W4'], '2. SH + CVD filter (all)'),
    (False, ['GZ','NG','Si','W4'],            '3. SH baseline (good only)'),
    (True,  ['GZ','NG','Si','W4'],            '4. SH + CVD (good only)'),
]

print(f'{"Config":25s} | Trades |   PnL   |  WR  |  PF  | Per ticker',flush=True)
print('-'*95,flush=True)
for use_cvd, tickers, label in configs:
    run(use_cvd, tickers, label)
