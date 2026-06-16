#!/usr/bin/env python3
"""Minimum viable monthly PnL tracker — исправленная версия с unbuffered output"""
import json, os, sys
from datetime import datetime
from collections import defaultdict
import numpy as np
import pandas as pd
import clickhouse_connect

sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000
PORTFOLIO = {
    'core': [('GL','vod','L',13,2),('MM','sm','L',21,2),('HY','vyf','L',8,3),
             ('NM','sm','L',21,3),('YD','vod','L',21,5),('NG','vou','L',5,5),
             ('AL','sm','L',21,2),('AF','vod','L',21,2),('PT','vod','L',21,3),
             ('RN','vou','L',13,2)],
    'hedge': [('SV','sm','S',5,5),('GLDRUBF','vyf','S',5,5),
              ('VB','vou','S',5,5),('SBERF','sm','S',21,2)],
}

def rz(s,w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

ch=clickhouse_connect.get_client(host='127.0.0.1',port=8123)
symbs=set()
for lst in PORTFOLIO.values():
    for c in lst: symbs.add(c[0])

print("Load...",end=' ',flush=True)
data={}
for s in symbs:
    q=f"SELECT p.time,p.open,p.high,p.low,p.close,p.volume,o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell FROM moex.prices_5m p LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol WHERE p.symbol='{s}' AND p.time>='2025-01-01' AND p.time<='2026-04-30' ORDER BY p.time"
    r=ch.query(q)
    if r.result_rows:
        cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
        df=pd.DataFrame(r.result_rows,columns=cols)
        df['time']=pd.to_datetime(df['time']); df['time']=df['time'].dt.tz_localize(None); df.set_index('time',inplace=True)
        data[s]=df

print(f"{len(data)} tickers",flush=True)

print("Signals...",end=' ',flush=True)
sig={}
for sym,df in data.items():
    d=df.copy()
    d['volume']=d['volume'].astype(float)
    d['vma']=d['volume'].rolling(20,min_periods=10).mean().fillna(d['volume'])
    d['vr']=d['volume']/d['vma'].clip(lower=1)
    d['vz']=rz(d['volume'],20)
    d['fn']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0)
    d['yn']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['fz']=rz(d['fn'],20); d['yz']=rz(d['yn'],20)
    d['oi']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi'].rolling(20,min_periods=10).mean()
    d['a14']=calc_atr(d); d['ap']=d['a14']/d['close'].clip(lower=1)*100
    
    for lst in PORTFOLIO.values():
        for c in lst:
            if c[0]!=sym: continue
            pat,di,hold,atm=c[1],c[2],c[3],c[4]
            dm=1 if di=='L' else -1
            if pat in ('vod','vou'):
                vs=np.clip((d['vr']-1.5)/3.0,0,1)
                os_=np.clip((d['oima']-d['oi'])/d['oima'].clip(lower=0.1),0,1) if pat=='vod' else np.clip((d['oi']-d['oima'])/d['oima'].clip(lower=0.1),0,1)
                raw=vs*0.6+os_*0.4
            elif pat=='sm':
                raw=np.clip(abs(d['yz'])/3.0,0,1)*0.7+np.clip(abs(d['fz'])/3.0,0,1)*0.3
            elif pat=='vyf':
                vs=np.clip((d['vr']-2.0)/4.0,0,1)
                ys=np.clip(d['yn']/max(d['yn'].std(),1)*dm,0,1)
                raw=vs*0.5+ys*0.5
            else: raw=np.clip((d['vr']-2.5)/5.0,0,1)
            af=np.clip(1-(d['ap']-0.3)/3.0,0,1)
            sc=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
            k=f"{pat}_{di}"
            if k not in sig: sig[k]={}
            sig[k][sym]=(sc,di,hold,atm)

print(f"{len(sig)} signal types",flush=True)

print("Sim...",flush=True)
cash=INITIAL_CAPITAL
kh=defaultdict(lambda:{'w':0,'l':0,'pnl':[]})
pos={}
trades=[]
mpnl=defaultdict(float)

all_ts=sorted({t for df in data.values() for t in df.index})
all_ts=[t for t in all_ts if 7<=t.hour<23]
print(f"Bars: {len(all_ts)}",flush=True)

sig_cache={}
for k,v in sig.items():
    for sym,(sc,di,hold,atm) in v.items():
        sig_cache[(sym,k)]=(sc,di,hold,atm)

for idx,ts in enumerate(all_ts):
    # Выходы
    for sym in list(pos.keys()):
        p=pos[sym]; rs=p[0]
        if rs not in data or ts not in data[rs].index: continue
        bar=data[rs].loc[ts]
        ep=None
        if p[1]=='L' and bar['low']<=p[2]: ep=p[2]
        elif p[1]=='S' and bar['high']>=p[2]: ep=p[2]
        if ep is None and p[4]>=p[3]: ep=bar['close']
        if ep is None and p[5] is not None:
            sc_key=(rs,p[5])
            if sc_key in sig_cache:
                sc_arr,_,_,_=sig_cache[sc_key]
                if ts in sc_arr.index and float(sc_arr.loc[ts])<0.10: ep=bar['close']
        if ep is not None:
            dm=1 if p[1]=='L' else -1
            pp=dm*(ep-p[6])/p[6]
            pr=pp*p[7]*p[8]
            cash+=pr
            mpnl[ts.strftime('%Y-%m')]+=pr
            trades.append(pr)
            if pr>0: kh[rs]['w']+=1
            else: kh[rs]['l']+=1
            kh[rs]['pnl'].append(pr)
            if len(kh[rs]['pnl'])>50: kh[rs]['pnl'].pop(0)
            del pos[sym]
    
    # Входы
    locked=sum(p[7]*p[8] for p in pos.values())
    avail=cash-locked
    if avail<=0: continue
    
    entries=[]
    for lst in PORTFOLIO.values():
        for c in lst:
            sym,pat,di,hold,atm=c[0],c[1],c[2],c[3],c[4]
            if sym in pos or sym not in data: continue
            sc_key=(sym,f"{pat}_{di}")
            if sc_key not in sig_cache: continue
            sc_arr,sc_di,sc_hold,sc_atm=sig_cache[sc_key]
            if ts not in sc_arr.index: continue
            score=float(sc_arr.loc[ts])
            if np.isnan(score) or score<(0.25 if di=='L' else 0.20): continue
            go=TICKER_CONFIGS.get(sym,{}).get('go',5000)
            k=kh[sym]
            kelly=0.40
            if k['w']+k['l']>=10:
                wr_=k['w']/max(k['w']+k['l'],1)
                aw=max(sum(p for p in k['pnl'] if p>0)/max(k['w'],1),1)
                al=max(abs(sum(p for p in k['pnl'] if p<0)/max(k['l'],1)),1)
                rr=aw/al if al>0 else 1.5
                kv=wr_-(1-wr_)/max(rr,0.5)
                kelly=max(0.40,min(kv,1.50))
            pct=min(kelly*score,0.35)
            mr=avail*pct
            ct=max(1,int(mr/go))
            if ct==0: continue
            atrv=0
            if sym in data and ts in data[sym].index:
                bar=data[sym].loc[ts]
                if 'a14' in bar: atrv=float(bar['a14'])
                elif 'atr14' in bar: atrv=float(bar['atr14'])
            if atrv==0 or np.isnan(atrv): continue
            ep=float(data[sym].loc[ts]['close'])
            sp=ep-atrv*atm if di=='L' else ep+atrv*atm
            entries.append((score,sym,di,hold,ct,ep,sp,go,pat))
    
    entries.sort(reverse=True)
    for score,sym,di,hold,ct,ep,sp,go,pat in entries[:5]:
        cost=ct*go
        if cost>avail: continue
        pos[sym]=(sym,di,sp,hold,0,pat,ep,go,ct)
        avail-=cost

# Close
for sym,p in list(pos.items()):
    rs=p[0]
    if rs in data:
        lb=data[rs].iloc[-1]
        dm=1 if p[1]=='L' else -1
        pp=dm*(lb['close']-p[6])/p[6]
        pr=pp*p[7]*p[8]
        cash+=pr
        mpnl[data[rs].index[-1].strftime('%Y-%m')]+=pr

tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
wins=sum(1 for t in trades if t>0)
tt=len(trades)
wr_=wins/tt*100 if tt>0 else 0
days=(all_ts[-1]-all_ts[0]).days if all_ts else 365
years=max(days/365.25,0.1)
ann=(cash/INITIAL_CAPITAL)**(1/max(years,0.1))-1

print(f"\n{'='*50}")
print("MONTHLY PnL")
print(f"{'='*50}")
neg_m=0; worst=('',0,0.0)
for m in sorted(mpnl.keys()):
    p=mpnl[m]; pp=p/INITIAL_CAPITAL*100
    sign='+' if p>=0 else ''
    print(f"{m:10} {sign}{p:>+10,.0f} ₽ {pp:>+7.1f}%")
    if p<0: neg_m+=1
    if worst[0]=='' or pp<worst[2]: worst=(m,p,pp)

print(f"\n{len(mpnl)} months, {neg_m} negative")
print(f"Worst: {worst[0]} ({worst[2]:+.1f}%)")
print(f"\nReturn: {tr:+.1f}% ({ann*100:+.1f}%/год)")
print(f"WR: {wr_:.1f}% ({wins}/{tt})")

# Save
os.makedirs('reports/phase5_monthly_pnl',exist_ok=True)
with open('reports/phase5_monthly_pnl/final_clean.json','w') as f:
    json.dump({
        'monthly_pnl':{m:round(mpnl[m],2) for m in sorted(mpnl.keys())},
        'worst_month':{'month':worst[0],'pnl_pct':worst[2]},
        'negative_months':neg_m,'total_months':len(mpnl),
        'return_pct':tr,'annual_return':ann*100,'n_trades':tt,
    },f,indent=2)

print(f"\n{'✅ PASS' if worst[2]>-30 else '❌ FAIL'}: worst month {worst[2]:.1f}% > -30%")
print(f"\nSaved: reports/phase5_monthly_pnl/final_clean.json")
