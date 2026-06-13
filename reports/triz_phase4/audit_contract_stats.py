#!/usr/bin/env python3
"""Print the contract count distribution for the correct params."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from datetime import datetime
from config import CH_HOST, CH_PORT, CH_DB

COMM = 4
RISK_PCT = 0.02
MAX_LOT = 5
MAX_LEV = 5.0

CBR_DATES = [f'{y}-{m:02d}-{d:02d}' for y,m,d in [
    (2024,2,16),(2024,3,22),(2024,4,26),(2024,6,7),(2024,7,26),
    (2024,9,13),(2024,10,25),(2024,12,20),
    (2025,2,14),(2025,3,21),(2025,4,25),(2025,6,13),
    (2025,7,25),(2025,9,12),(2025,10,24),(2025,12,19),
    (2026,2,14),(2026,3,21),(2026,4,25)]]

def is_cbr(d):
    dt = datetime.strptime(d[:10],'%Y-%m-%d')
    return any(abs((dt-datetime.strptime(c,'%Y-%m-%d')).days)<=2 for c in CBR_DATES)

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_ticker_data(ticker):
    d_rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open,p.time), argMax(p.high,p.time), argMax(p.low,p.time),
               argMax(p.close,p.time), argMax(p.volume,p.time),
               argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
               argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
               argMax(o.total_oi,p.time)
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    if len(d_rows) < 60: return None
    
    a = np.array([list(r) for r in d_rows], dtype=object)
    dates = [str(r[0]) for r in d_rows]
    opn=a[:,1].astype(float); high=a[:,2].astype(float); low=a[:,3].astype(float)
    close=a[:,4].astype(float); vol=a[:,5].astype(float)
    yb=a[:,6].astype(float); ys=a[:,7].astype(float)
    fb=a[:,8].astype(float); fs=a[:,9].astype(float); toi=a[:,10].astype(float)
    
    toi=np.where(toi<=0,1,toi)
    v_m=np.mean(vol)+1; yb_m=np.mean(yb)+1; ys_m=np.mean(ys)+1; toi_m=np.mean(toi)+1
    dv=np.diff(vol)/v_m; dyb=np.diff(yb)/yb_m; dys=np.diff(ys)/ys_m; dtoi=np.diff(toi)/toi_m
    fiz_net=(fb-fs)/toi*100; dfn=np.diff(fiz_net)
    
    tr=np.zeros(len(close))
    tr[1:]=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    atr=np.full(len(close),np.nan)
    if len(close)>=15:
        atr_s=np.convolve(tr,np.ones(14)/14,mode='valid')[:len(close)]
        for i in range(14,len(close)): atr[i]=atr_s[i-14]
    
    sma50=np.full(len(close),np.nan)
    if len(close)>=50:
        cs=np.cumsum(close); sma50[49]=cs[49]/50; sma50[50:]=(cs[50:]-cs[:-50])/50
    
    cbr_filter=np.array([not is_cbr(d) for d in dates])
    dv_mag=np.abs(dv)
    
    return dict(dates=dates,opn=opn,high=high,low=low,close=close,vol=vol,
                dv=dv,dyb=dyb,dys=dys,dfn=dfn,dtoi=dtoi,sma50=sma50,atr=atr,
                cbr_filter=cbr_filter,dv_mag=dv_mag,n=len(d_rows))

def vol_up_oi_up_yb_up(dv,dyb,dys,dfn,dtoi):
    return dv>0 and dtoi>0 and dyb>0

data = load_ticker_data('GL')
trades_list = []
cap = 8696
eq = float(cap)
hold = 13

dates=data['dates']; opn=data['opn']; high=data['high']; low=data['low']
close=data['close']; dv=data['dv']; dyb=data['dyb']; dys=data['dys']
dfn=data['dfn']; dtoi=data['dtoi']; sma50=data['sma50']; atr=data['atr']
cbr_f=data['cbr_filter']; dv_mag=data['dv_mag']; n=len(close)

for i in range(max(50,15), n-max(hold,2)):
    if i>=len(dv): break
    if not vol_up_oi_up_yb_up(dv[i],dyb[i],dys[i],dfn[i],dtoi[i]): continue
    if dv_mag[i]<0: continue
    if i<len(cbr_f) and not cbr_f[i]: continue
    if sma50 is not None and i<len(sma50) and not np.isnan(sma50[i]) and close[i]<=sma50[i]:
        continue
    
    ei=i+1
    if ei>=n-1: continue
    ep=float(opn[ei])
    xi=min(ei+hold, n-1)
    
    go=ep*1
    if go<=0: continue
    
    sl_pct = 0.005  # The hidden parameter!
    risk_amount=eq*0.02
    base_nc=max(1,int(risk_amount/(go*sl_pct)))
    nc=min(base_nc, 5)
    max_by_go=int(eq*5.0/go) if go>0 else 99
    nc=min(nc, max_by_go)
    if nc<1: continue
    
    remaining_nc=nc
    npnl_total=0
    exit_date=dates[xi]; xp=float(close[xi]); stop_hit=False
    
    running_high=ep
    sp=ep*(1-min(max(atr[i]/ep*2,0.01),0.05))
    for j in range(ei, xi+1):
        bh=float(high[j])
        if bh>running_high:
            running_high=bh
            if j<len(atr) and not np.isnan(atr[j]):
                new_trail=max(atr[j]/running_high*2,0.01)
            else:
                new_trail=0.01
            sp=max(sp, running_high*(1-min(new_trail,0.05)))
        
        if float(low[j])<=sp:
            xp=sp; stop_hit=True; exit_date=dates[j]
            npnl_total+=remaining_nc*(xp-ep)-remaining_nc*4
            remaining_nc=0
            break
    
    if not stop_hit and remaining_nc>0:
        npnl_total+=remaining_nc*(xp-ep)-remaining_nc*4
    
    eq+=npnl_total
    
    trades_list.append(dict(
        entry=dates[ei], ep=ep, nc=nc, 
        leverage=f"{nc*ep/(eq-npnl_total):.2f}x",
        npnl=round(npnl_total,0),
        stop=stop_hit
    ))

# Contract stats
ncs = [t['nc'] for t in trades_list]
print(f"CONTRACT COUNT DISTRIBUTION (cap=8696, sl_pct=0.005)")
print(f"Total trades: {len(trades_list)}")
for c in sorted(set(ncs)):
    print(f"  nc={c}: {ncs.count(c)} trades ({ncs.count(c)/len(ncs)*100:.0f}%)")
print(f"  Min: {min(ncs)}, Max: {max(ncs)}, Mean: {np.mean(ncs):.1f}")

# Leverage stats
levs = [float(t['leverage'].replace('x','')) for t in trades_list]
print(f"\nLEVERAGE STATS")
print(f"  Min: {min(levs):.2f}x, Max: {max(levs):.2f}x, Mean: {np.mean(levs):.2f}x")
print(f"  Trades above 3x: {sum(1 for l in levs if l > 3)}/{len(levs)}")
print(f"  Trades above 5x: {sum(1 for l in levs if l > 5)}/{len(levs)}")
