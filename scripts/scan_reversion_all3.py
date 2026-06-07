#!/usr/bin/env python3
"""Full reversion scan — single connection, 6 months."""
import psycopg2, numpy as np, sys, time
from datetime import datetime, timedelta

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
SINCE = (datetime.now()-timedelta(days=180)).strftime('%Y-%m-%d')

def zs(vals, w=20):
    out = np.zeros(len(vals))
    for i in range(w, len(vals)):
        c = vals[i-w:i]; mu=c.mean(); sd=c.std(ddof=0)
        out[i] = (vals[i]-mu)/sd if sd>0 else 0.0
    return out

t0 = time.time()
conn = psycopg2.connect(**DB)
cur = conn.cursor()
cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m WHERE time>=%s ORDER BY symbol", (SINCE,))
tickers = [r[0] for r in cur.fetchall()]

print(f"🔍 Mean Reversion: {len(tickers)} tickers, 6 months", flush=True)
print(f"{'Тикер':6s} | {'Сигн':>4s} | {'WR%':>5s} | {'PF':>5s} | {'DD%':>5s} | {'Напр':5s} | {'Score':>6s}", flush=True)
print("-"*55, flush=True)

results = []
for idx, sym in enumerate(tickers):
    cur.execute("""
        SELECT open, high, low, close, volume 
        FROM moex_prices_5m 
        WHERE symbol=%s AND time>=%s 
        ORDER BY time
    """, (sym, SINCE))
    rows = cur.fetchall()
    
    if len(rows) < 200: continue
    
    n=len(rows)
    c=np.array([float(r[3]) for r in rows])
    o=np.array([float(r[0]) for r in rows])
    hi=np.array([float(r[1]) for r in rows])
    lo=np.array([float(r[2]) for r in rows])
    v=np.array([float(r[4] or 0) for r in rows])
    
    rng = hi-lo; wz=zs(v,20); pos=(c-lo)/np.maximum(rng,0.001); mr=np.median(rng)
    if mr == 0: continue
    
    best = None
    for ml,mh in [(0.3,0.7),(0.2,0.8)]:
        for hz in [6,12,24]:
            cand = np.where((wz>=1.5)&(rng>=mr*1.5)&(pos>=ml)&(pos<=mh))[0]
            cand = cand[(cand>=25)&(cand<n-hz)]
            if len(cand)<10: continue
            
            lr, sr = [], []
            for i in cand:
                pc = c[i-3:i]-o[i-3:i]
                if np.all(pc>0): sr.append((c[i]-c[i+hz])/c[i]*100)
                elif np.all(pc<0): lr.append((c[i+hz]-c[i])/c[i]*100)
            
            for lab,rets in [('BOTH',lr+sr),('LONG',lr),('SHORT',sr)]:
                if len(rets)<12: continue
                wr=sum(1 for r in rets if r>0)/len(rets)*100
                g=sum(r for r in rets if r>0); ls=abs(sum(r for r in rets if r<0))
                pf=g/ls if ls>0 else 0; dd=0.0; c2=p2=0.0
                for rv in rets:
                    c2+=rv
                    if c2>p2: p2=c2
                    dd=max(dd,p2-c2)
                if pf>=1.15 and (best is None or wr>best[0] and pf>=best[1]):
                    best=(wr,pf,len(rets),dd,lab,hz,ml,mh)
    
    if best:
        wr,pf,ns,dd,lab,hz,ml,mh = best
        score = wr*pf/max(dd,0.1)
        st = '✅' if pf>=1.5 else ('🟡' if pf>=1.3 else '🔸')
        print(f"{st} {sym:6s} | {ns:4d} | {wr:5.1f}% | {pf:5.2f} | {dd:5.1f}% | {lab:5s} | {score:6.1f}", flush=True)
        results.append((sym,wr,pf,ns,dd,lab,hz,score))
    elif (idx+1) % 10 == 0 or idx == len(tickers)-1:
        print(f"[{idx+1}/{len(tickers)}] {sym:6s} ❌ ({time.time()-t0:.0f}s)", flush=True)

cur.close(); conn.close()

print(f"\n{'='*55}", flush=True)
print(f"TOP 15 по Score — {time.time()-t0:.0f}s", flush=True)
print(f"{'='*55}", flush=True)
results.sort(key=lambda x:-x[7])
for sym,wr,pf,ns,dd,lab,hz,score in results[:15]:
    st = '✅' if pf>=1.5 else '🟡'
    print(f"{st} {sym:6s} | WR={wr:5.1f}% PF={pf:.2f} n={ns:4d} DD={dd:.1f}% {lab:5s} Score={score:.1f}", flush=True)

print(f"\nТикеров: {len(tickers)}, рабочих: {len(results)}", flush=True)
