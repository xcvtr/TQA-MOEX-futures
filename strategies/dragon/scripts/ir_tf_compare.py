#!/usr/bin/env python3 -u
"""IR Si sweep — 1m vs 3m vs 5m vs 10m, scaled params."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='Si' AND bt>='2025-07-16' ORDER BY bt").result_rows
ch.close()

bars = []
for r in rows:
    ts=r[0]; h,m=ts.hour,ts.minute
    if ts.weekday()>=5: continue
    if h<15 or h>23 or (h==23 and m>45): continue
    bars.append({'ts':ts,'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4]),'vol':float(r[5])})
print(f'Si: {len(bars)} M1 bars', flush=True)

SPEC={'ms':1.0,'sp':1.0,'go':6662}

def resample_n(bars, n):
    g={}
    for b in bars:
        tm=b['ts'].hour*60+b['ts'].minute; km=(tm//n)*n
        k=b['ts'].replace(minute=km%60,hour=km//60,second=0)
        if k not in g: g[k]={'ts':k,'opn':b['opn'],'hi':b['hi'],'lo':b['lo'],'prc':b['prc']}
        else: gg=g[k]; gg['hi']=max(gg['hi'],b['hi']); gg['lo']=min(gg['lo'],b['lo']); gg['prc']=b['prc']
    return sorted(g.values(),key=lambda x:x['ts'])

for tf in [3, 5, 10]:
    dbars=resample_n(bars,tf)
    d2m={}; di=0
    for mi in range(len(bars)):
        if di<len(dbars) and bars[mi]['ts']>=dbars[di]['ts']: d2m[di]=mi; di+=1
    
    # Scaled params for this TF (keep window = 12 min, cooldown = 12 min, TO = 12 min)
    ib = max(2, int(12 / tf))  # ~12 min impulse window
    cd = max(2, int(12 / tf))  # ~12 min cooldown
    to = max(2, int(12 / tf))  # ~12 min timeout
    ta, tt, sl = 0.005, 0.003, 0.007  # keep same trailing % (price-based, not bar-based)
    
    print(f'\n=== Si detect={tf}мин ib={ib} cd={cd} to={to} ===', flush=True)
    
    for rp in [1,2,3,4,5,7,10]:
        risk=rp/100.0; reset_state()
        eq=200000.0; peak=eq; mtm_pk=eq; cdd=mdd=0; pos=None; tr=[]
        df=set(); dxi=0; ms,sp,go=SPEC['ms'],SPEC['sp'],SPEC['go']
        par={'impulse_bars':ib,'impulse_pct':0.3,'cooldown':cd,'min_vol_pct':0}
        
        for mi in range(60,len(bars)):
            b=bars[mi]
            # Tick first (manage positions from prev bars)
            if pos:
                ex=None; slv=pos['ep']*(1-sl) if pos['dir']=='long' else pos['ep']*(1+sl)
                if (pos['dir']=='long' and b['lo']<=slv) or (pos['dir']=='short' and b['hi']>=slv): ex=slv
                if not ex:
                    if not pos.get('tr'):
                        act=pos['ep']*(1+ta) if pos['dir']=='long' else pos['ep']*(1-ta)
                        if (pos['dir']=='long' and b['hi']>=act) or (pos['dir']=='short' and b['lo']<=act):
                            pos['tr']=True; pos['tl']=b['hi']*(1-tt) if pos['dir']=='long' else b['lo']*(1+tt)
                    if pos.get('tr') and ((pos['dir']=='long' and b['lo']<=pos['tl']) or (pos['dir']=='short' and b['hi']>=pos['tl'])): ex=pos['tl']
                if not ex and mi-pos['bi']>=to: ex=b['prc']
                if ex:
                    pnl=(ex-pos['ep'])/ms*sp*(-1 if pos['dir']=='short' else 1)*pos['shares']-4*pos['shares']
                    eq+=pnl; tr.append(pnl); peak=max(peak,eq); cdd=max(cdd,(peak-eq)/peak*100); pos=None
            # Detect
            if not pos and dxi<len(dbars) and dxi not in df and mi>=d2m.get(dxi,999999999):
                df.add(dxi); db=dbars[dxi]; dh=dbars[:dxi]
                if len(dh)>=20:
                    bd={'prc':db['prc'],'hi':db['hi'],'lo':db['lo'],'vol':100,
                        'bars_list':dh,'lo_hist':[x['lo'] for x in dh],
                        'hi_hist':[x['hi'] for x in dh],'close_hist':[x['prc'] for x in dh],
                        'vol_hist':[100]*len(dh)}
                    sig=ir_check(bd,'Si',par)
                    if sig:
                        sh=max(1,int(eq*risk/go))
                        b_vol=bars[mi]['vol'] if mi<len(bars) else 999999
                        if b_vol>0: sh=min(sh,max(1,int(b_vol*0.1)))
                        if go*sh<=eq:
                            slip=ms; ep2=sig['entry_price']+(slip if sig['direction']=='long' else -slip)
                            pos={'dir':sig['direction'],'ep':ep2,'bi':mi,'shares':sh,'tr':False}
                dxi+=1
            # MTM
            fl=(b['prc']-pos['ep'])/ms*sp*(-1 if pos['dir']=='short' else 1)*pos['shares'] if pos else 0
            mv=eq+fl; mtm_pk=max(mtm_pk,mv); mdd=max(mdd,(mtm_pk-mv)/mtm_pk*100) if mtm_pk>0 else 0
        
        n=len(tr)
        if n:
            w=[p for p in tr if p>0]; l=[p for p in tr if p<=0]
            wr=len(w)/n*100; pf=sum(w)/sum(abs(p)for p in l) if l else 0; rt=(eq-200000)/200000*100
            mk=' ✅' if mdd<=20 else ''
            print(f'{rp:3d}%  {rt:>+8.1f}%  {mdd:>6.2f}%  {pf:>5.2f}  {wr:5.1f}%  {n:4d}{mk}', flush=True)
