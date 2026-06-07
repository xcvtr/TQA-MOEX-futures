#!/usr/bin/env python3
"""Mean Reversion After Volatility Expansion — fast scan."""
import psycopg2, sys, numpy as np
from datetime import datetime, timedelta

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
TICKERS = ['HS', 'KC', 'DX', 'HY', 'BM', 'AF', 'MC', 'CC']

def zs(vals, w=20):
    out = [0.0]*len(vals)
    for i in range(w, len(vals)):
        c = vals[i-w:i]; mu=sum(c)/w; sd=(sum((x-mu)**2 for x in c)/w)**0.5
        out[i] = (vals[i]-mu)/sd if sd>0 else 0.0
    return out

def calc_dd(rets):
    c=p=dd=0.0
    for r in rets:
        c+=r
        if c>p: p=c
        d=p-c
        if d>dd: dd=d
    return dd

for sym in TICKERS:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    cur.execute("""
        SELECT p.time,p.open,p.high,p.low,p.close,p.volume
        FROM moex_prices_5m p WHERE p.symbol=%s AND p.time>=%s ORDER BY p.time
    """, (sym, since))
    rows = cur.fetchall()
    cur.close(); conn.close()
    if len(rows) < 200: continue
    
    n = len(rows)
    opens  = np.array([float(r[1]) for r in rows])
    highs  = np.array([float(r[2]) for r in rows])
    lows   = np.array([float(r[3]) for r in rows])
    closes = np.array([float(r[4]) for r in rows])
    volumes = np.array([float(r[5] or 0) for r in rows])
    
    ranges = highs - lows
    pos = (closes - lows) / np.maximum(ranges, 0.001)
    vol_z = zs(volumes.tolist(), 20)
    
    # Quick grid: fewer combos
    best = None
    for lb in [2, 3]:
        for mid_low,mid_high in [(0.3,0.7),(0.2,0.8),(0.4,0.6)]:
            for ran_mul in [1.3, 1.8]:
                for h in [6, 12, 24]:
                    rets = []
                    for i in range(30, n - h):
                        if vol_z[i] < 1.5: continue
                        med_r = float(np.median(ranges[max(0,i-50):i]))
                        if ranges[i] < med_r * ran_mul: continue
                        if not (mid_low <= pos[i] <= mid_high): continue
                        
                        prev = closes[i-lb:i]
                        if all(prev[j] > prev[j-1] for j in range(1,lb)):
                            ret = (closes[i] - closes[i+h]) / closes[i] * 100
                            rets.append(ret)
                        elif all(prev[j] < prev[j-1] for j in range(1,lb)):
                            ret = (closes[i+h] - closes[i]) / closes[i] * 100
                            rets.append(ret)
                    
                    if len(rets) < 15: continue
                    w = sum(1 for r in rets if r>0); ns=len(rets)
                    wr=w/ns*100; g=sum(r for r in rets if r>0)
                    l=abs(sum(r for r in rets if r<0))
                    pf=g/l if l>0 else 0; dd=calc_dd(rets)
                    
                    if pf > 1.2 and ns >= 20 and (best is None or wr > best[0]):
                        best = (wr, pf, ns, dd, lb, mid_low, mid_high, ran_mul, h)
    
    if best:
        wr,pf,ns,dd,lb,ml,mh,rm,h = best
        status = "✅" if pf > 1.3 else "🟡"
        print(f"{sym:5s} | {status} n={ns:4d} WR={wr:5.1f}% PF={pf:.2f} DD={dd:.1f}%  lb={lb} rng≥{rm:.0f}×med mid=[{ml},{mh}] h={h}")
    else:
        print(f"{sym:5s} | ❌ no passing")
