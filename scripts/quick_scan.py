#!/usr/bin/env python3
"""Quick scan remaining tickers."""
import psycopg2, sys
import numpy as np

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
TICKERS = ['AF', 'BR', 'GD', 'NG', 'VB', 'PD', 'GAZPF']
THRESHOLDS = [1.5, 2.0, 2.5, 3.0]
EXITS = [3, 6, 12, 24, 48]
MIN_SIG = 20

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

conn = psycopg2.connect(**DB)
cur = conn.cursor()

for sym in TICKERS[:4]:
    cur.execute("""
        SELECT oi.fiz_buy,oi.fiz_sell,oi.yur_buy,oi.yur_sell,p.close,p.volume,p.open
        FROM moex_prices_5m_oi oi JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
        WHERE oi.symbol=%s AND oi.time>='2023-01-01' ORDER BY oi.time
    """, (sym,))
    rows = cur.fetchall()
    print(f"\n{sym}: {len(rows)} rows")
    if len(rows) < 500: continue
    
    n=len(rows)
    c=[float(r[4]or 0) for r in rows]
    v=[float(r[5]or 0) for r in rows]
    o=[float(r[6]or 0) for r in rows]
    fn=[float((r[0]or 0)-(r[1]or 0)) for r in rows]
    yn=[float((r[2]or 0)-(r[3]or 0)) for r in rows]
    
    vz=zs(v,20); fz=zs(fn,20); yz=zs(yn,20); mh=max(EXITS)
    
    for vt in [2.0, 2.5, 3.0]:
        for dt in [1.0, 1.5]:
            if vt<dt: continue
            for h in EXITS:
                rets=[]
                for i in range(20, n-mh-1):
                    if vz[i]<vt: continue
                    if abs(fz[i])<dt or abs(yz[i])<dt: continue
                    if fz[i]*yz[i]>=0: continue
                    ep=o[i+1]
                    if ep<=0: continue
                    if i+1+h-1>=n: continue
                    xp=c[i+1+h-1]
                    if xp<=0: continue
                    ret=(xp-ep)/ep*100 if yz[i]>0 else (ep-xp)/ep*100
                    rets.append(ret)
                
                if len(rets)<MIN_SIG: continue
                w=sum(1 for r in rets if r>0); ns=len(rets)
                wr=w/ns*100; g=sum(r for r in rets if r>0)
                l=abs(sum(r for r in rets if r<0))
                pf=g/l if l>0 else 99.9
                ar=sum(rets)/ns; dd=calc_dd(rets)
                score=round(wr*pf/100,1)
                
                if wr>=55 and pf>=1.3 and dd<=25:
                    print(f"  ✅ V≥{vt} D≥{dt} h={h:2d}: n={ns:4d} WR={wr:5.1f}% PF={pf:5.2f} avg={ar:+6.2f}% DD={dd:5.1f}%")
                elif pf>=1.3 and wr>=50:
                    print(f"  🟡 V≥{vt} D≥{dt} h={h:2d}: n={ns:4d} WR={wr:5.1f}% PF={pf:5.2f} avg={ar:+6.2f}% DD={dd:5.1f}%")

conn.close()
