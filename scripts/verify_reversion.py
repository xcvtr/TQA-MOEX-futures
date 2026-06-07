#!/usr/bin/env python3
import psycopg2, numpy as np
from datetime import datetime, timedelta
DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')
SINCE = (datetime.now()-timedelta(days=180)).strftime('%Y-%m-%d')
def zs(vals, w=20):
    out = np.zeros(len(vals))
    for i in range(w, len(vals)):
        c = vals[i-w:i]; mu=np.mean(c); sd=np.std(c, ddof=0)
        out[i] = (vals[i]-mu)/sd if sd>0 else 0.0
    return out

tickers = ['NM','NR','BR','MM','SBERF','HS','KC','DX']
conn = psycopg2.connect(**DB)
cur = conn.cursor()
for sym in tickers:
    cur.execute('SELECT open,high,low,close,volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s ORDER BY time', (sym, SINCE))
    rows = cur.fetchall()
    if len(rows) < 200: continue
    n=len(rows)
    c=np.array([float(r[3]) for r in rows]); o=np.array([float(r[0]) for r in rows])
    hi=np.array([float(r[1]) for r in rows]); lo=np.array([float(r[2]) for r in rows])
    v=np.array([float(r[4] or 0) for r in rows])
    rng=hi-lo; wz=zs(v,20); pos=(c-lo)/np.maximum(rng,0.001); mr=float(np.median(rng))
    if mr==0: continue
    for entry_mode in ['close','open_next']:
        best = None
        for ml,mh in [(0.3,0.7),(0.2,0.8)]:
            for hz in [12,24]:
                cand = np.where((wz>=1.5)&(rng>=mr*1.5)&(pos>=ml)&(pos<=mh))[0]
                cand = cand[(cand>=25)&(cand<n-hz-1)]
                if len(cand)<8: continue
                lr, sr = [], []
                for i in cand:
                    pc = c[i-3:i]-o[i-3:i]
                    entry = c[i] if entry_mode=='close' else o[i+1]
                    if entry<=0: continue
                    if np.all(pc>0): sr.append((entry-c[i+hz])/entry*100)
                    elif np.all(pc<0): lr.append((c[i+hz]-entry)/entry*100)
                for lab,rets in [('BOTH',lr+sr),('LONG',lr),('SHORT',sr)]:
                    if len(rets)<10: continue
                    wr=sum(1 for r in rets if r>0)/len(rets)*100
                    g=sum(r for r in rets if r>0); ls=abs(sum(r for r in rets if r<0))
                    pf=g/ls if ls>0 else 0; dd=0.0; cu=pk=0.0
                    for rv in rets:
                        cu+=rv
                        if cu>pk: pk=cu
                        dd=max(dd,pk-cu)
                    if pf>=1.15 and (best is None or wr>best[0]):
                        best=(wr,pf,len(rets),dd,lab,hz,ml,mh)
        if best:
            wr,pf,ns,dd,lab,hz,ml,mh = best
            print(f"{sym:>6s} | {entry_mode:10s} | n={ns:3d} WR={wr:5.1f}% PF={pf:.2f} DD={dd:5.1f}% {lab:6s} h={hz}")
        else:
            print(f"{sym:>6s} | {entry_mode:10s} | FAILED")
cur.close(); conn.close()
