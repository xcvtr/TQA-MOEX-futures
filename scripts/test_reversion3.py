#!/usr/bin/env python3
"""Fast reversion test — single query for all tickers."""
import psycopg2, numpy as np, sys
from datetime import datetime, timedelta

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
TICKERS = ['HS','KC','DX','HY','BM','AF','MC','CC']
SINCE = (datetime.now()-timedelta(days=365)).strftime('%Y-%m-%d')

def zs(vals, w=20):
    out = np.zeros(len(vals))
    for i in range(w, len(vals)):
        c = vals[i-w:i]; mu=c.mean(); sd=c.std(ddof=0)
        out[i] = (vals[i]-mu)/sd if sd>0 else 0.0
    return out

# Single batch query
conn = psycopg2.connect(**DB)
cur = conn.cursor()
placeholders = ','.join(['%s']*len(TICKERS))
cur.execute(f"""
    SELECT symbol, time, open, high, low, close, volume
    FROM moex_prices_5m
    WHERE symbol IN ({placeholders}) AND time>=%s
    ORDER BY symbol, time
""", TICKERS + [SINCE])
all_rows = cur.fetchall()
cur.close(); conn.close()

# Group by symbol
from collections import defaultdict
by_sym = defaultdict(list)
for r in all_rows:
    by_sym[r[0]].append(r)

print(f"{'Тикер':5s} | {'Статус':6s} | {'n':>4s} | {'WR%':>5s} | {'PF':>5s} | {'DD%':>5s} | {'Напр':5s} | {'Параметры':40s}")
print("-"*85)

for sym in TICKERS:
    rows = by_sym.get(sym, [])
    if len(rows) < 200:
        print(f"{sym:5s} | {'❌':6s} | мало данных")
        continue
    
    n=len(rows)
    c=np.array([float(r[5]) for r in rows])
    o=np.array([float(r[2]) for r in rows])  # open
    hi=np.array([float(r[3]) for r in rows])  # high
    lo=np.array([float(r[4]) for r in rows])  # low
    v=np.array([float(r[6] or 0) for r in rows])  # volume
    
    rng = hi - lo
    wz = zs(v, 20)
    pos_in_range = (c - lo) / np.maximum(rng, 0.001)
    
    best = None
    
    # Test 2 hypotheses
    for mid_low,mid_high in [(0.3,0.7),(0.2,0.8),(0.4,0.6)]:
        for hz in [6, 12, 24]:
            # Find exhaustion bars: high volume + big range + close in middle
            cand = np.where(
                (wz >= 1.5) & 
                (rng >= np.median(rng) * 1.5) & 
                (pos_in_range >= mid_low) & 
                (pos_in_range <= mid_high)
            )[0]
            cand = cand[(cand >= 25) & (cand < n - hz)]
            
            long_rets, short_rets = [], []
            for i in cand:
                # Check 3-bar direction before exhaustion
                prev_ch = c[i-3:i] - o[i-3:i]
                if np.all(prev_ch > 0):
                    short_rets.append((c[i] - c[i+hz]) / c[i] * 100)
                elif np.all(prev_ch < 0):
                    long_rets.append((c[i+hz] - c[i]) / c[i] * 100)
            
            for label, rets in [('LONG', long_rets), ('SHORT', short_rets), ('BOTH', long_rets+short_rets)]:
                if len(rets) < 15: continue
                wr = sum(1 for r in rets if r>0)/len(rets)*100
                g=sum(r for r in rets if r>0); l_sum=abs(sum(r for r in rets if r<0))
                pf=g/l_sum if l_sum>0 else 0
                
                # DD
                dd_ret = 0.0; cum=peak=0.0
                for rv in rets:
                    cum+=rv
                    if cum>peak: peak=cum
                    dd_ret=max(dd_ret, peak-cum)
                
                if pf > 1.1 and len(rets) >= 20 and (best is None or wr > best[0]):
                    best = (wr, pf, len(rets), dd_ret, label, mid_low, mid_high, hz)
    
    if best:
        wr,pf,ns,dd,label,ml,mh,hz = best
        st = '✅' if pf > 1.3 else '🟡'
        params = f"mid=[{ml},{mh}] h={hz}"
        print(f"{sym:5s} | {st:6s} | {ns:4d} | {wr:5.1f}% | {pf:5.2f} | {dd:5.1f}% | {label:5s} | {params:40s}")
    else:
        print(f"{sym:5s} | {'❌':6s} | no passing")
