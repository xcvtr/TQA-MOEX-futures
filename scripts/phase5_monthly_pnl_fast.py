#!/usr/bin/env python3
"""Monthly PnL: ultra-fast, только счёт сделок по месяцам, без MTM."""
import json
from datetime import datetime
from collections import defaultdict
import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000
PORTFOLIO = {
    'core': [('GL','vod','L',13,2,1.0),('MM','sm','L',21,2,1.0),('HY','vyf','L',8,3,1.0),
             ('NM','sm','L',21,3,1.0),('YD','vod','L',21,5,1.0),('NG','vou','L',5,5,1.0),
             ('AL','sm','L',21,2,1.0),('AF','vod','L',21,2,1.0),('PT','vod','L',21,3,1.0),
             ('RN','vou','L',13,2,1.0)],
    'hedge': [('SV','sm','S',5,5,1.0),('GLDRUBF','vyf','S',5,5,1.0),
              ('VB','vou','S',5,5,1.0),('SBERF','sm','S',21,2,1.0)],
}

def rz(s,w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

ch=clickhouse_connect.get_client(host='127.0.0.1',port=8123)
symbols=set()
for lst in PORTFOLIO.values(): symbols.update(c[0] for c in lst)

print("Loading data...")
data={}
for sym in symbols:
    q=f"SELECT p.time,p.open,p.high,p.low,p.close,p.volume,o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell FROM moex.prices_5m p LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol WHERE p.symbol='{sym}' AND p.time>='2025-01-01' AND p.time<='2026-04-30' ORDER BY p.time"
    r=ch.query(q)
    if r.result_rows:
        cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
        df=pd.DataFrame(r.result_rows,columns=cols)
        df['time']=pd.to_datetime(df['time']); df.set_index('time',inplace=True)
        data[sym]=df
        print(f"  {sym}: {len(df)} bars")

print("Precomputing signals (one-pass per ticker)...")
signals={}
for sym,df in data.items():
    d=df.copy()
    d['volume']=d['volume'].astype(float)
    d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr']=d['volume']/d['vma20'].clip(lower=1); d['vz']=rz(d['volume'],20)
    d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0)
    d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['fz']=rz(d['fiz_net'],20); d['yz']=rz(d['yur_net'],20)
    d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100
    sigs={}; seen=set()
    for lst in PORTFOLIO.values():
        for c in lst:
            sn,pat,di,hold,atm=c[0],c[1],c[2],c[3],c[4]
            if sn!=sym: continue
            k=f"{pat}_{di}"
            if k in seen: continue
            seen.add(k)
            dm=1 if di=='L' else -1
            if pat in ('vod','vou'):
                vs=np.clip((d['vr']-1.5)/3.0,0,1)
                os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1) if pat=='vod' else np.clip((d['oi_r']-d['oima'])/d['oima'].clip(lower=0.1),0,1)
                raw=vs*0.6+os_*0.4
            elif pat=='sm':
                raw=np.clip(abs(d['yz'])/3.0,0,1)*0.7+np.clip(abs(d['fz'])/3.0,0,1)*0.3
            elif pat=='vyf':
                vs=np.clip((d['vr']-2.0)/4.0,0,1)
                ys=np.clip(d['yur_net'].fillna(0)/max(d['yur_net'].std(),1)*dm,0,1)
                raw=vs*0.5+ys*0.5
            else: raw=np.clip((d['vr']-2.5)/5.0,0,1)
            af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
            score=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
            dout=d.copy(); dout['score']=score
            sigs[k]=(dout,di,hold,atm)
    signals[sym]=sigs
    print(f"  {sym} done")

print("\nRunning simulation (NO MTM, only trade counting)...")
cash=INITIAL_CAPITAL; peak=INITIAL_CAPITAL; max_dd=0
kh=defaultdict(lambda:{'w':0,'l':0,'pnl':[]})
pos={}; monthly_pnl=defaultdict(float); trades=[]

all_ts=sorted({t for df in data.values() for t in df.index})
print(f"Bars: {len(all_ts)}")

for idx,ts in enumerate(all_ts):
    # Выходы
    to_close=[]
    for sym,p in list(pos.items()):
        rs=p.get('real_sym',sym)
        if rs not in data or ts not in data[rs].index: continue
        bar=data[rs].loc[ts]
        ep=None; dr=''
        if p['dir']=='L' and bar['low']<=p['stop']: ep=p['stop']; dr='stop'
        elif p['dir']=='S' and bar['high']>=p['stop']: ep=p['stop']; dr='stop'
        if ep is None and p.get('bars_held',0)>=p.get('hold',40): ep=bar['close']; dr='time'
        if ep is None and 'pattern' in p:
            sk=f"{p['pattern']}_{p['dir']}"
            if rs in signals and sk in signals[rs]:
                dfs,_,_,_=signals[rs][sk]
                if ts in dfs.index and float(dfs.loc[ts,'score'])<0.10: ep=bar['close']; dr='fade'
        if ep is not None:
            dm=1 if p['dir']=='L' else -1
            pp=dm*(ep-p['entry'])/p['entry']
            pr=pp*p['go']*p['contracts']; cash+=pr
            monthly_pnl[ts.strftime('%Y-%m')]+=pr
            trades.append({'sym':rs,'dir':p['dir'],'pnl_rub':pr})
            if pr>0: kh[rs]['w']+=1
            else: kh[rs]['l']+=1
            kh[rs]['pnl'].append(pr)
            if len(kh[rs]['pnl'])>50: kh[rs]['pnl'].pop(0)
            to_close.append(sym)
    for s in to_close: del pos[s]
    
    # Входы
    if ts.hour<7 or ts.hour>=23: continue
    locked=sum(p['go']*p.get('contracts',0) for p in pos.values())
    avail=cash-locked
    if avail<=0: continue
    entries=[]
    for lst_name,lst in PORTFOLIO.items():
        for sym,pat,di,hold,atm,w in lst:
            if sym in pos or sym not in data: continue
            if sym not in signals: continue
            sk=f"{pat}_{di}"
            if sk not in signals[sym]: continue
            dfs,_,_,_=signals[sym][sk]
            if ts not in dfs.index: continue
            bs=dfs.loc[ts]
            score=float(bs.get('score',0))
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
            pct=min(kelly*score*w,0.35)
            mr=avail*pct
            ct=max(1,int(mr/go))
            if ct==0: continue
            atrv=float(bs.get('atr14',0))
            if atrv==0 or np.isnan(atrv): continue
            ep=float(bs['close'])
            stop=ep-atrv*atm if di=='L' else ep+atrv*atm
            entries.append((sym,pat,di,hold,ct,ep,stop,go,score))
    entries.sort(key=lambda e:e[8],reverse=True)
    for ent in entries[:5]:
        sym,pat,di,hold,ct,ep,stop,go,score=ent
        cost=ct*go
        if cost>avail: continue
        pos[sym]={'real_sym':sym,'dir':di,'hold':hold,'entry':ep,'stop':stop,'contracts':ct,'go':go,'bars_held':0,'entry_ts':ts,'pattern':pat}
        avail-=cost

# Close остатки
for sym,p in list(pos.items()):
    rs=p.get('real_sym',sym)
    if rs in data:
        lb=data[rs].iloc[-1]
        dm=1 if p['dir']=='L' else -1
        pp=dm*(lb['close']-p['entry'])/p['entry']
        pr=pp*p['go']*p['contracts']; cash+=pr
        monthly_pnl[data[rs].index[-1].strftime('%Y-%m')]+=pr

# Stats
tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
wins=sum(1 for t in trades if t.get('pnl_rub',0)>0)
tt=len(trades)
wr_=wins/tt*100 if tt>0 else 0
days=(all_ts[-1]-all_ts[0]).days if all_ts else 365
years=max(days/365.25,0.1)
ann=(cash/INITIAL_CAPITAL)**(1/max(years,0.1))-1

print(f"\n{'='*50}")
print("MONTHLY PnL")
print(f"{'='*50}")
print(f"{'Month':10} {'PnL':>12} {'%':>8}")
neg_m=0; worst=('',0,0)
for m in sorted(monthly_pnl.keys()):
    pnl=monthly_pnl[m]; cap_start=INITIAL_CAPITAL
    pp=pnl/cap_start*100
    print(f"{m:10} {pnl:>+10,.0f} ₽ {pp:>+7.1f}%")
    if pnl<0: neg_m+=1
    if worst[0]=='' or pp<worst[2]: worst=(m,pnl,pp)

print(f"\nMonths: {len(monthly_pnl)}, negative: {neg_m}")
print(f"Worst: {worst[0]} ({worst[2]:+.1f}%)")
print(f"\nTotal: {tr:+.1f}% ({ann*100:+.1f}%/год)")
print(f"WR: {wr_:.1f}% ({wins}/{tt})")

worst_pct=worst[2] if worst[0] else 0
if worst_pct > -30:
    print(f"\n✅ PASS: worst month {worst_pct:.1f}% > -30%")
else:
    print(f"\n❌ FAIL: worst month {worst_pct:.1f}% < -30%")

os.makedirs('reports/phase5_monthly_pnl',exist_ok=True)
with open('reports/phase5_monthly_pnl/final.json','w') as f:
    json.dump({
        'monthly_pnl':{m:round(monthly_pnl[m],2) for m in sorted(monthly_pnl.keys())},
        'worst_month':{'month':worst[0],'pnl_pct':worst[2]},
        'negative_months':neg_m,'total_months':len(monthly_pnl),
        'return_pct':tr,'annual_return':ann*100,'n_trades':tt,
    },f,indent=2)
print("\nSaved: reports/phase5_monthly_pnl/final.json")
