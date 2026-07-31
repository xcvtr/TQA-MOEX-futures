#!/usr/bin/env python3 -u
"""IR Si 1m — TRIZ grid: test filters to improve WR."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='Si' AND bt>='2026-01-16' ORDER BY bt").result_rows
ch.close()
bars = []
for r in rows:
    ts=r[0]; h,m=ts.hour,ts.minute
    if ts.weekday()>=5: continue
    if h<15 or h>23 or (h==23 and m>45): continue
    bars.append({'ts':ts,'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4]),'vol':float(r[5])})
print(f'Si: {len(bars)} M1 bars', flush=True)

GO, MS, SP = 6453, 1.0, 1.0
TA, TT, SL, TO = 0.005, 0.003, 0.007, 12
TC = 4
RISK = 0.01

def resample_n(m1, n):
    g={}
    for b in m1:
        tm=b['ts'].hour*60+b['ts'].minute; km=(tm//n)*n
        k=b['ts'].replace(minute=km%60,hour=km//60,second=0)
        if k not in g: g[k]={'ts':k,'opn':b['opn'],'hi':b['hi'],'lo':b['lo'],'prc':b['prc']}
        else: gg=g[k]; gg['hi']=max(gg['hi'],b['hi']); gg['lo']=min(gg['lo'],b['lo']); gg['prc']=b['prc']
    return sorted(g.values(),key=lambda x:x['ts'])

def run_test(params, label):
    """Run IR Si 1m with optional filters. Returns {n, wr, pf, ret, mdd}."""
    reset_state()
    par={'impulse_bars':12,'impulse_pct':params.get('impulse_pct',0.3),'cooldown':12,'min_vol_pct':0}
    trend = params.get('trend', False)
    min_vol_ratio = params.get('min_vol_ratio', 0)  # min vol / median vol
    loss_skip = params.get('loss_skip', 0)  # skip after N consecutive losses
    session_start = params.get('session_start', 15)
    session_end = params.get('session_end', 24)
    impulse_min = params.get('impulse_min', 0)  # min impulse override
    
    dbars = resample_n(bars, 1)
    d2m={}; di=0
    for mi in range(len(bars)):
        if di<len(dbars) and bars[mi]['ts']>=dbars[di]['ts']: d2m[di]=mi; di+=1
    
    # Precompute median volumes
    vols = [b['vol'] for b in bars]
    med_vol = np.median(vols) if vols else 1
    
    eq=200000.0; peak=eq; mtm_pk=eq; cdd=mdd=0; pos=None; trades=[]
    df=set(); dxi=0
    consec_losses = 0
    medians = {}  # cache for median vol per detect bar
    
    for mi in range(60, len(bars)):
        b=bars[mi]
        # Tick
        if pos:
            ex=None; slv=pos['ep']*(1-SL)
            if b['lo']<=slv: ex=slv
            if not ex:
                if not pos.get('tr'):
                    if b['hi']>=pos['ep']*(1+TA): pos['tr']=True; pos['tl']=b['hi']*(1-TT)
                if pos.get('tr') and b['lo']<=pos['tl']: ex=pos['tl']
            if not ex and mi-pos['bi']>=TO: ex=b['prc']
            if ex:
                pnl=(ex-pos['ep'])*pos['shares']-TC*pos['shares']
                eq+=pnl; trades.append(pnl); peak=max(peak,eq); cdd=max(cdd,(peak-eq)/peak*100)
                if pnl>0: consec_losses=0
                else: consec_losses+=1
                pos=None
        # Detect
        if not pos and dxi<len(dbars) and dxi not in df and mi>=d2m.get(dxi,999999999):
            df.add(dxi); db=dbars[dxi]; dh=dbars[:dxi]
            if len(dh)>=20:
                # Session filter
                h = bars[mi]['ts'].hour
                if h < session_start or h >= session_end:
                    dxi+=1; continue
                # Loss streak filter
                if loss_skip > 0 and consec_losses >= loss_skip:
                    dxi+=1; continue
                
                bd={'prc':db['prc'],'hi':db['hi'],'lo':db['lo'],'vol':100,
                    'bars_list':dh,'lo_hist':[x['lo'] for x in dh],
                    'hi_hist':[x['hi'] for x in dh],'close_hist':[x['prc'] for x in dh],
                    'vol_hist':[100]*len(dh)}
                sig=ir_check(bd,'Si',par)
                if sig:
                    # Volume filter
                    if min_vol_ratio > 0:
                        dxi_vols = [x['vol'] for x in dh[-20:]]
                        avg_vol = np.mean(dxi_vols) if dxi_vols else 0
                        if avg_vol < med_vol * min_vol_ratio:
                            dxi+=1; continue
                    # Trend filter (SMA50 on M1)
                    if trend and len(bars[:mi]) >= 50:
                        last50 = [x['prc'] for x in bars[mi-50:mi]]
                        sma50 = sum(last50)/50
                        if sig['direction']=='long' and db['prc']<sma50:
                            dxi+=1; continue
                        if sig['direction']=='short' and db['prc']>sma50:
                            dxi+=1; continue
                    
                    sh=max(1,int(eq*RISK/GO))
                    bv=b['vol'] if mi<len(bars) else 999999
                    if bv>0: sh=min(sh,max(1,int(bv*0.1)))
                    if GO*sh<=eq:
                        slip=1; ep2=sig['entry_price']+(slip if sig['direction']=='long' else -slip)
                        pos={'dir':sig['direction'],'ep':ep2,'bi':mi,'shares':sh,'tr':False}
            dxi+=1
        fl=(b['prc']-pos['ep'])*pos['shares'] if pos else 0
        mv=eq+fl; mtm_pk=max(mtm_pk,mv); mdd=max(mdd,(mtm_pk-mv)/mtm_pk*100) if mtm_pk>0 else 0
    
    n=len(trades)
    if n:
        w=[p for p in trades if p>0]; l=[p for p in trades if p<=0]
        wr=len(w)/n*100; pf=sum(w)/sum(abs(p)for p in l) if l else 0; rt=(eq-200000)/200000*100
        return {'n':n,'wr':wr,'pf':pf,'ret':rt,'mdd':mdd}
    return {'n':0,'wr':0,'pf':0,'ret':0,'mdd':0}

# Baseline
print('\n=== BASELINE (risk=1%) ===')
r = run_test({}, 'baseline')
print(f'{r}\n')

# Grid of filters (reduced for speed)
filters = [
    ('BASELINE', {}),
    ('TREND SMA50', {'trend': True}),
    ('VOL > 0.8 med', {'min_vol_ratio': 0.8}),
    ('SKIP 2 loss', {'loss_skip': 2}),
    ('TREND+VOL', {'trend': True, 'min_vol_ratio': 0.8}),
    ('TREND+SKIP2', {'trend': True, 'loss_skip': 2}),
]

print(f'{"FILTER":>22s}  {"n":>5s}  {"WR%":>6s}  {"PF":>5s}  {"ROI%":>8s}  {"MDD%":>6s}')
print('-'*58)
for name, params in filters:
    r = run_test(params, name)
    print(f'{name:>22s}  {r["n"]:5d}  {r["wr"]:5.1f}%  {r["pf"]:>5.2f}  {r["ret"]:>+7.1f}%  {r["mdd"]:5.2f}%')
