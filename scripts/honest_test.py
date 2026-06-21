#!/usr/bin/env python3
"""
Честный тест: BASE v2 (LONG vs LONG+SHORT) с правильным contracts.
contracts = int(cash * lot_pct / go), если <1 — пропускаем.
lot=25% как в оригинале (безопасно). Все метрики корректны.
"""
import sys, os, time, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL = 100_000
SYMBOLS = ['GL', 'HS', 'HY', 'RN', 'NM', 'AF']
LOT = 0.25  # 25% — как в проверенном portfolio_sweep_enhancements.py
START, END = '2024-01-01', '2026-05-01'


def rz(s,w=20): m=s.rolling(w,min_periods=w).mean();std=s.rolling(w,min_periods=w).std();return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1); tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def load_data(sym):
    ch=clickhouse_connect.get_client(host=CH_HOST,port=CH_PORT,database=CH_DB)
    q=f"SELECT p.time,p.open,p.high,p.low,p.close,p.volume,o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell,o.total_oi FROM moex.prices_5m p LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol WHERE p.symbol='{sym}' AND p.time>='2023-01-01' AND p.time<='2026-05-01' ORDER BY p.time"
    r=ch.query(q); cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df=pd.DataFrame(r.result_rows,columns=cols); df['time']=pd.to_datetime(df['time']).dt.tz_localize(None); df.set_index('time',inplace=True)
    return df

def load_accounts(sym):
    ch=clickhouse_connect.get_client(host=CH_HOST,port=CH_PORT,database=CH_DB)
    q=f"SELECT time,clgroup,buy_accounts,sell_accounts FROM moex.openinterest WHERE symbol='{sym}' AND time>='2023-01-01' AND time<='2026-05-01' ORDER BY time,clgroup"
    r=ch.query(q); rows=r.result_rows
    if not rows: return pd.DataFrame()
    recs=[{'time':r[0],'clg':r[1],'buy_a':r[2],'sell_a':r[3]} for r in rows]
    df=pd.DataFrame(recs); df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
    fiz=df[df['clg']==0][['time','buy_a','sell_a']].rename(columns={'buy_a':'fiz_buy_a','sell_a':'fiz_sell_a'})
    yur=df[df['clg']==1][['time','buy_a','sell_a']].rename(columns={'buy_a':'yur_buy_a','sell_a':'yur_sell_a'})
    merged=pd.merge(fiz,yur,on='time',how='outer').fillna(0); merged.set_index('time',inplace=True)
    return merged

def precompute(df, acc_df=None):
    d=df.copy()
    d['volume']=d['volume'].astype(float)
    d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr']=d['volume']/d['vma20'].clip(lower=1)
    d['vz']=rz(d['volume'],20)
    d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0); d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
    d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100
    af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)

    # Symmetrical score for LONG+SHORT
    vs_sym=np.tanh(d['vz']/3)
    os_sym=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),-1,1)
    d['score_sym']=np.clip((vs_sym*0.3+os_sym*0.7)*af,-1,1)

    # LONG-only score (как в проверенном portfolio_sweep_enhancements.py)
    vs=np.clip((d['vr']-1.5)/3.0,0,1)
    os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
    raw=vs*0.6+os_*0.4
    score=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
    d['score']=score
    d['score_conf']=d['score']
    return d

def simulate(d, use_short, sym):
    mask=(d.index>=pd.Timestamp(START))&(d.index<pd.Timestamp(END))
    dd=d[mask].copy()
    if len(dd)<100: return None

    cash=float(INITIAL); peak=float(INITIAL); max_dd=0.0; trades=0; wins=0
    go=TICKER_CONFIGS.get(sym,{}).get('go',5000); pos=None

    for i in range(1,len(dd)):
        bar=dd.iloc[i]; h=bar.name.hour
        if h<7 or h>=23: continue

        if pos is not None:
            pos['bars_left']-=1; hit=False; ep=float(bar['close'])
            if pos['dir']=='L' and float(bar['low'])<=pos['stop']: hit=True; ep=pos['stop']
            elif pos['dir']=='S' and float(bar['high'])>=pos['stop']: hit=True; ep=pos['stop']
            elif pos['bars_left']<=0: hit=True
            if hit:
                dm=1 if pos['dir']=='L' else -1
                pp=dm*(ep-pos['entry'])/pos['entry']
                pr=pp*pos['go']*pos['contracts']
                cash+=pr; trades+=1
                if pr>0: wins+=1; pos=None

        if pos is not None:
            dm=1 if pos['dir']=='L' else -1
            mtm=dm*(float(bar['close'])-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq=cash+mtm
        else: teq=cash
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        if pos is not None: continue

        if use_short:
            s=float(bar['score_sym'])
            if np.isnan(s) or abs(s)<0.10: continue
            direction='L' if s>0 else 'S'
        else:
            s=float(bar['score'])
            if np.isnan(s) or s<0.25: continue  # порог 0.25 как в оригинале
            direction='L'

        # Правильный contracts: если не хватает на 1 контракт — пропускаем
        potential=cash*LOT
        if potential<go: continue  # cash < 2×GO при LOT=0.50
        contracts=int(potential/go)
        if contracts<1: continue

        atrv=float(bar.get('atr14',1)); ep=float(bar['close'])
        stop_p=ep-atrv*1.0 if direction=='L' else ep+atrv*1.0
        pos={'dir':direction,'entry':ep,'stop':stop_p,'bars_left':8,'go':go,'contracts':contracts}

    if pos is not None:
        lb=dd.iloc[-1]; dm=1 if pos['dir']=='L' else -1
        pp=dm*(float(lb['close'])-pos['entry'])/pos['entry']; pr=pp*pos['go']*pos['contracts']
        cash+=pr; trades+=1
        if pr>0: wins+=1

    tr=(cash-INITIAL)/INITIAL*100
    days=max((pd.Timestamp(END)-pd.Timestamp(START)).days,30); yrs_=days/365.25
    cagr=((cash/INITIAL)**(1/max(yrs_,0.1))-1)*100 if cash>0 else -100
    calmar=tr/100/max(max_dd,0.001) if max_dd>0 else tr*10
    return {'ret':round(tr,2),'cagr':round(cagr,2),'dd':round(max_dd*100,2),'calmar':round(calmar,2),'wr':round(wins/trades*100,2) if trades>0 else 0,'trades':trades,'final_cash':round(cash,2)}


def main():
    print("="*100)
    print(f"ЧЕСТНЫЙ ТЕСТ: LOT={LOT*100:.0f}% капитала, contracts проверка cash>=GO")
    print(f"Период: {START} — {END}")
    print("="*100, flush=True)

    loaded={}
    for sym in SYMBOLS:
        t0=time.time(); df=load_data(sym); acc=load_accounts(sym); loaded[sym]=precompute(df,acc)
        print(f"  {sym}: {len(loaded[sym])} баров за {time.time()-t0:.1f}s", flush=True)

    print(f"\\n{'='*100}")
    print(f"LONG ONLY (score>0.25, LOT=25%)")
    print(f"{'='*100}")
    print(f"{'Тикер':<6}{'Ret%':>9}{'DD%':>7}{'Calmar':>9}{'WR%':>6}{'CAGR%':>8}{'Tr':>7}{'Cash':>10}")
    print('-'*62)
    for sym in SYMBOLS:
        r=simulate(loaded[sym],False,sym)
        if r: print(f"{sym:<6}{r['ret']:>8.1f}%{r['dd']:>6.1f}%{r['calmar']:>9.1f}{r['wr']:>5.1f}%{r['cagr']:>7.1f}%{r['trades']:>7}{r['final_cash']:>10.0f}")
        print(f"  {sym} done", flush=True)

    print(f"\\n{'='*100}")
    print(f"LONG+SHORT (|score_sym|>0.10, LOT=25%)")
    print(f"{'='*100}")
    print(f"{'Тикер':<6}{'Ret%':>9}{'DD%':>7}{'Calmar':>9}{'WR%':>6}{'CAGR%':>8}{'Tr':>7}{'Cash':>10}")
    print('-'*62)
    for sym in SYMBOLS:
        r=simulate(loaded[sym],True,sym)
        if r: print(f"{sym:<6}{r['ret']:>8.1f}%{r['dd']:>6.1f}%{r['calmar']:>9.1f}{r['wr']:>5.1f}%{r['cagr']:>7.1f}%{r['trades']:>7}{r['final_cash']:>10.0f}")
        print(f"  {sym} done", flush=True)

    # Сравнение
    print(f"\\n{'='*100}")
    print(f"СРАВНЕНИЕ")
    print(f"{'='*100}")
    print(f"{'Тикер':<6}{'L Ret':>8}{'LS Ret':>8}{'L DD':>6}{'LS DD':>6}{'L Calm':>8}{'LS Calm':>8}{'L Tr':>6}{'LS Tr':>6}")
    print('-'*62)
    for sym in SYMBOLS:
        rl=simulate(loaded[sym],False,sym); rls=simulate(loaded[sym],True,sym)
        if rl and rls:
            dc=rls['calmar']-rl['calmar']; icon='🟢' if dc>0.5 else ('🔴' if dc<-0.5 else '➡️')
            print(f"{sym:<6}{rl['ret']:>7.1f}%{rls['ret']:>7.1f}%{rl['dd']:>5.1f}%{rls['dd']:>5.1f}%{rl['calmar']:>8.1f}{rls['calmar']:>8.1f}{rl['trades']:>6}{rls['trades']:>6} {icon}")


if __name__=='__main__':
    main()
