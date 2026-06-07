#!/usr/bin/env python3
"""Fast reversion test — vectorized, minimal loops."""
import psycopg2, numpy as np
from datetime import datetime, timedelta

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
TICKERS = ['HS','KC','DX','HY','BM','AF','MC','CC']

def zs(vals, w=20):
    out = np.zeros(len(vals))
    for i in range(w, len(vals)):
        c = vals[i-w:i]; mu=c.mean(); sd=c.std(ddof=0)
        out[i] = (vals[i]-mu)/sd if sd>0 else 0.0
    return out

def test_ticker(sym):
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    since = (datetime.now()-timedelta(days=365)).strftime('%Y-%m-%d')
    cur.execute("SELECT p.time,p.open,p.high,p.low,p.close,p.volume FROM moex_prices_5m p WHERE p.symbol=%s AND p.time>=%s ORDER BY p.time", (sym, since))
    rows = cur.fetchall()
    cur.close(); conn.close()
    if len(rows)<200: return None
    
    n=len(rows); c=np.array([float(r[4]) for r in rows])
    o=np.array([float(r[1]) for r in rows])
    r=np.array([float(r[2])-float(r[3]) for r in rows])
    v=np.array([float(r[5] or 0) for r in rows])
    vz=zs(v,20); pos=(c-np.minimum(o,c))/np.maximum(r,0.001)
    
    best=None
    # Only 2 params: close position range, exit horizon
    for mid in [(0.3,0.7),(0.2,0.8),(0.25,0.75)]:
        for h in [6,12,24]:
            # Vectorized: find all candidates
            cond = (vz>=1.5) & (r>=np.median(r)*1.5) & (pos>=mid[0]) & (pos<=mid[1])
            idx = np.where(cond)[0]
            idx = idx[(idx>=25) & (idx<n-h)]
            
            if len(idx)<15: continue
            
            long_rets, short_rets = [], []
            for i in idx:
                prev_ch = c[i-3:i] - o[i-3:i]
                if np.all(prev_ch>0):  # 3 bars up
                    short_rets.append((c[i]-c[i+h])/c[i]*100)
                elif np.all(prev_ch<0):  # 3 bars down
                    long_rets.append((c[i+h]-c[i])/c[i]*100)
            
            for name, rets in [('LONG',long_rets),('SHORT',short_rets),('BOTH',long_rets+short_rets)]:
                if len(rets)<15: continue
                wins=sum(1 for r in rets if r>0); ns=len(rets); wr=wins/ns*100
                g=sum(r for r in rets if r>0); l=abs(sum(r for r in rets if r<0))
                pf=g/l if l>0 else 0
                dd=0; cum=peak=0.0
                for rv in rets: cum+=rv
                if cum>peak: peak=cum
                dd=max(dd,peak-cum)
                
                if pf>1.1 and ns>=20 and (best is None or wr>best[0]):
                    best=(wr,pf,ns,dd,name,h,mid[0],mid[1])
    
    return best

for sym in TICKERS:
    r=test_ticker(sym)
    if r: print(f"{sym:5s} | {'✅' if r[1]>1.3 else '🟡'} n={r[2]:4d} WR={r[0]:5.1f}% PF={r[1]:.2f} DD={r[3]:.1f}% {r[4]:6s} h={r[5]:2d} mid=[{r[6]},{r[7]}]")
    else: print(f"{sym:5s} | ❌")
