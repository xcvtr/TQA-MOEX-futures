#!/usr/bin/env python3
"""Full scan: Mean Reversion After Volatility Expansion — ALL tickers."""
import psycopg2, numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')
SINCE = (datetime.now()-timedelta(days=365)).strftime('%Y-%m-%d')

def zs(vals, w=20):
    out = np.zeros(len(vals))
    for i in range(w, len(vals)):
        c = vals[i-w:i]; mu=c.mean(); sd=c.std(ddof=0)
        out[i] = (vals[i]-mu)/sd if sd>0 else 0.0
    return out

# Load all tickers with sufficient data
conn = psycopg2.connect(**DB)
cur = conn.cursor()
cur.execute("""
    SELECT symbol, time, open, high, low, close, volume
    FROM moex_prices_5m
    WHERE time>=%s
    ORDER BY symbol, time
""", (SINCE,))
all_rows = cur.fetchall()
cur.close(); conn.close()

by_sym = defaultdict(list)
for r in all_rows:
    by_sym[r[0]].append(r)

tickers = sorted(by_sym.keys())
print(f"🔍 Mean Reversion Scan: {len(tickers)} tickers, {len(all_rows)} rows total")
print(f"{'Тикер':6s} | {'Статус':6s} | {'n':>4s} | {'WR%':>5s} | {'PF':>5s} | {'DD%':>5s} | {'Напр':6s} | {'h':>2s} | {'Score':>6s}")
print("-" * 70)

results = []
for sym in tickers:
    rows = by_sym[sym]
    n=len(rows)
    if n < 200:
        continue
    
    c=np.array([float(r[5]) for r in rows])
    o=np.array([float(r[2]) for r in rows])
    hi=np.array([float(r[3]) for r in rows])
    lo=np.array([float(r[4]) for r in rows])
    v=np.array([float(r[6] or 0) for r in rows])
    
    rng = hi - lo
    wz = zs(v, 20)
    pos = (c - lo) / np.maximum(rng, 0.001)
    med_rng = np.median(rng)
    
    best = None
    
    for ml,mh in [(0.3,0.7),(0.2,0.8),(0.4,0.6),(0.25,0.75)]:
        for hz in [6, 12, 24]:
            # Exhaustion: high vol + wide range + close mid-body
            cand = np.where(
                (wz >= 1.5) & 
                (rng >= med_rng * 1.5) & 
                (pos >= ml) & (pos <= mh)
            )[0]
            cand = cand[(cand >= 25) & (cand < n - hz)]
            
            long_rets, short_rets = [], []
            for i in cand:
                prev_ch = c[i-3:i] - o[i-3:i]
                if np.all(prev_ch > 0):
                    short_rets.append((c[i] - c[i+hz]) / c[i] * 100)
                elif np.all(prev_ch < 0):
                    long_rets.append((c[i+hz] - c[i]) / c[i] * 100)
            
            for label, rets in [('BOTH', long_rets+short_rets), ('LONG', long_rets), ('SHORT', short_rets)]:
                if len(rets) < 15: continue
                wr = sum(1 for r in rets if r>0)/len(rets)*100
                g=sum(r for r in rets if r>0); lo_sum=abs(sum(r for r in rets if r<0))
                pf=g/lo_sum if lo_sum>0 else 0
                
                dd=0.0; cum=peak=0.0
                for rv in rets:
                    cum+=rv
                    if cum>peak: peak=cum
                    dd=max(dd, peak-cum)
                
                score = wr * pf / dd if dd > 0 else 0
                
                if pf >= 1.2 and (best is None or wr > best[0] and pf > best[1]):
                    best = (wr, pf, len(rets), dd, label, ml, mh, hz, score)
    
    if best:
        wr,pf,ns,dd,label,ml,mh,hz,score = best
        st = '✅' if pf >= 1.3 else ('🟡' if pf >= 1.15 else '❌')
        print(f"{sym:6s} | {st:6s} | {ns:4d} | {wr:5.1f}% | {pf:5.2f} | {dd:5.1f}% | {label:6s} | {hz:2d} | {score:6.1f}")
        results.append((sym, wr, pf, ns, dd, label, hz, score))
    
print(f"\n{'='*70}")
print(f"TOP 20 по Score (WR × PF / DD)")
print(f"{'='*70}")
results.sort(key=lambda x: -x[7])
for sym,wr,pf,ns,dd,label,hz,score in results[:20]:
    st = '✅' if pf >= 1.3 else '🟡'
    print(f"{st} {sym:6s} | WR={wr:5.1f}% PF={pf:.2f} n={ns:4d} DD={dd:.1f}% {label:6s} h={hz:2d} Score={score:.1f}")
