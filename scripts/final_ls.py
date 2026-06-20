#!/usr/bin/env python3
"""
Финальный LONG vs LONG+SHORT на проверенном симуляторе.
Период: 2025-01-01 — 2026-05-01
LONG: score>0.25, stop=2ATR, bars=13, lev=0.25
L+S:  |score_sym|>0.10, stop=1ATR, bars=8
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB
from datetime import datetime

INITIAL = 100_000
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']
START, END = datetime(2025,1,1), datetime(2026,5,1)


def rz(s,w=20): m=s.rolling(w,min_periods=w).mean();std=s.rolling(w,min_periods=w).std();return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1); tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def load_data(sym):
    ch=clickhouse_connect.get_client(host=CH_HOST,port=CH_PORT,database=CH_DB)
    q=f"SELECT p.time,p.open,p.high,p.low,p.close,p.volume,o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell,o.total_oi FROM moex.prices_5m p LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-04-30' ORDER BY p.time"
    r=ch.query(q)
    cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df=pd.DataFrame(r.result_rows,columns=cols); df['time']=pd.to_datetime(df['time']).dt.tz_localize(None); df.set_index('time',inplace=True)
    return df

def precompute(d):
    d['volume']=d['volume'].astype(float)
    d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr']=d['volume']/d['vma20'].clip(lower=1); d['vz']=rz(d['volume'],20)
    d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0); d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100
    vs=np.clip((d['vr']-1.5)/3.0,0,1); os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
    raw=vs*0.6+os_*0.4; af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
    d['score']=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
    # Sym score
    vs_sym=np.tanh(d['vz']/3); os_sym=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),-1,1)
    d['score_sym']=np.clip((vs_sym*0.3+os_sym*0.7)*af,-1,1)
    return d

def sim_long(d, sym):
    mask=(d.index>=START)&(d.index<END); dd=d[mask].copy()
    cash=INITIAL; peak=INITIAL; max_dd=0; trades=0; wins=0
    go=TICKER_CONFIGS.get(sym,{}).get('go',5000); pos=None
    for i in range(1,len(dd)):
        bar=dd.iloc[i]; h=bar.name.hour
        if h<7 or h>=23: continue
        if pos:
            pos['bars_left']-=1; hit=False; ep=bar['close']
            if pos['dir']=='L' and bar['low']<=pos['stop']: hit=True; ep=pos['stop']
            elif pos['dir']=='S' and bar['high']>=pos['stop']: hit=True; ep=pos['stop']
            elif pos['bars_left']<=0: hit=True
            if hit:
                dm=1 if pos['dir']=='L' else -1; pp=dm*(ep-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
                cash+=pp; trades+=1; wins+=1 if pp>0 else 0; pos=None
        if pos:
            dm=1 if pos['dir']=='L' else -1
            mtm=dm*(bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq=cash+mtm
        else: teq=cash
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        if pos: continue
        s=float(bar['score'])
        if np.isnan(s) or s<0.25: continue
        contracts=max(1,int(cash*0.25/go))
        atrv=float(bar.get('atr14',1)); ep=float(bar['close'])
        pos={'dir':'L','entry':ep,'stop':ep-atrv*2,'bars_left':13,'go':go,'contracts':contracts}
    if pos:
        lb=dd.iloc[-1]; dm=1 if pos['dir']=='L' else -1
        pp=dm*(lb['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
        cash+=pp; trades+=1; wins+=1 if pp>0 else 0
    tr=(cash-INITIAL)/INITIAL*100; days=(END-START).days; yrs=max(days/365.25,0.1)
    cagr=((cash/INITIAL)**(1/max(yrs,0.1))-1)*100 if cash>0 else -100
    calmar=tr/100/max(max_dd,0.001) if max_dd>0 else tr*10
    return {'ret':round(tr,2),'cagr':round(cagr,2),'dd':round(max_dd*100,2),'calmar':round(calmar,2),'wr':round(wins/trades*100,2) if trades>0 else 0,'trades':trades}

def sim_ls(d, sym):
    mask=(d.index>=START)&(d.index<END); dd=d[mask].copy()
    cash=INITIAL; peak=INITIAL; max_dd=0; trades=0; wins=0
    go=TICKER_CONFIGS.get(sym,{}).get('go',5000); pos=None
    for i in range(1,len(dd)):
        bar=dd.iloc[i]; h=bar.name.hour
        if h<7 or h>=23: continue
        if pos:
            pos['bars_left']-=1; hit=False; ep=bar['close']
            if pos['dir']=='L' and bar['low']<=pos['stop']: hit=True; ep=pos['stop']
            elif pos['dir']=='S' and bar['high']>=pos['stop']: hit=True; ep=pos['stop']
            elif pos['bars_left']<=0: hit=True
            if hit:
                dm=1 if pos['dir']=='L' else -1; pp=dm*(ep-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
                cash+=pp; trades+=1; wins+=1 if pp>0 else 0; pos=None
        if pos:
            dm=1 if pos['dir']=='L' else -1
            mtm=dm*(bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq=cash+mtm
        else: teq=cash
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        if pos: continue
        s=float(bar['score_sym'])
        if np.isnan(s) or abs(s)<0.10: continue
        direction='L' if s>0 else 'S'
        contracts=max(1,int(cash*0.25/go))
        atrv=float(bar.get('atr14',1)); ep=float(bar['close'])
        stop=ep-atrv*1.0 if direction=='L' else ep+atrv*1.0
        pos={'dir':direction,'entry':ep,'stop':stop,'bars_left':8,'go':go,'contracts':contracts}
    if pos:
        lb=dd.iloc[-1]; dm=1 if pos['dir']=='L' else -1
        pp=dm*(lb['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
        cash+=pp; trades+=1; wins+=1 if pp>0 else 0
    tr=(cash-INITIAL)/INITIAL*100; days=(END-START).days; yrs=max(days/365.25,0.1)
    cagr=((cash/INITIAL)**(1/max(yrs,0.1))-1)*100 if cash>0 else -100
    calmar=tr/100/max(max_dd,0.001) if max_dd>0 else tr*10
    return {'ret':round(tr,2),'cagr':round(cagr,2),'dd':round(max_dd*100,2),'calmar':round(calmar,2),'wr':round(wins/trades*100,2) if trades>0 else 0,'trades':trades}

def main():
    print("="*100)
    print(f"ФИНАЛ: LONG vs LONG+SHORT (проверенный симулятор)")
    print(f"{START.date()} — {END.date()}")
    print("="*100, flush=True)

    loaded={}
    for sym in SYMBOLS:
        t0=time.time(); df=load_data(sym); loaded[sym]=precompute(df)
        print(f"  {sym}: {len(loaded[sym])} баров за {time.time()-t0:.1f}s", flush=True)

    print(f"\n{'Тикер':<6}{'Long Ret':>9}{'LS Ret':>9}{'Long DD':>7}{'LS DD':>7}{'L Cal':>7}{'LS Cal':>7}{'L Tr':>6}{'LS Tr':>6}")
    print('-'*64)
    for sym in SYMBOLS:
        rl=sim_long(loaded[sym],sym); rls=sim_ls(loaded[sym],sym)
        dc=rls['calmar']-rl['calmar']
        icon='🟢' if dc>0.5 else ('🔴' if dc<-0.5 else '➡️')
        print(f"{sym:<6}{rl['ret']:>8.1f}%{rls['ret']:>8.1f}%{rl['dd']:>6.1f}%{rls['dd']:>6.1f}%{rl['calmar']:>7.1f}{rls['calmar']:>7.1f}{rl['trades']:>6}{rls['trades']:>6} {icon}")
        print(f"  {sym} done", flush=True)

if __name__=='__main__':
    main()
