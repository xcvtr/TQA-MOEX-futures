#!/usr/bin/env python3
"""
Тест confidence factor из openinterest:
- accounts_per_volume: fiz_vol / fiz_accounts (концентрация)
- yur_accounts_change: рост/падение числа юр-счетов

Sigma: 1 тикер GL, быстро.
"""
import sys, os, json
from datetime import datetime
from collections import defaultdict
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS
from config import CH_HOST, CH_PORT, CH_DB

INITIAL_CAPITAL = 100_000
TEST_START = datetime(2025, 1, 1)
TEST_END = datetime(2026, 5, 1)
SYMBOL = 'GL'

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def load_data(sym):
    ch=get_ch()
    q=f"""
        SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
               o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell,o.total_oi
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='2026-04-30'
        ORDER BY p.time
    """
    r=ch.query(q)
    cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
    df=pd.DataFrame(r.result_rows,columns=cols)
    df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time',inplace=True)
    return df

def load_accounts(sym):
    """Грузим openinterest с clg=0 (fiz) и clg=1 (yur) — buy_accounts/sell_accounts"""
    ch=get_ch()
    q=f"""
        SELECT time, clgroup, buy_accounts, sell_accounts
        FROM moex.openinterest
        WHERE symbol='{sym}' AND time>='2024-01-01' AND time<='2026-04-30'
        ORDER BY time, clgroup
    """
    r=ch.query(q)
    rows=r.result_rows
    if not rows:
        return pd.DataFrame()
    
    data=[]
    for row in rows:
        data.append({'time':row[0], 'clg':row[1], 'buy_a':row[2], 'sell_a':row[3]})
    df=pd.DataFrame(data)
    df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
    
    # Пивот: clg=0→fiz, clg=1→yur
    fiz=df[df['clg']==0][['time','buy_a','sell_a']].rename(columns={'buy_a':'fiz_buy_a','sell_a':'fiz_sell_a'})
    yur=df[df['clg']==1][['time','buy_a','sell_a']].rename(columns={'buy_a':'yur_buy_a','sell_a':'yur_sell_a'})
    merged=pd.merge(fiz,yur,on='time',how='outer').fillna(0)
    merged.set_index('time',inplace=True)
    return merged

def precompute(df, acc_df=None):
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
    
    # OI score (vod_L)
    vs=np.clip((d['vr']-1.5)/3.0,0,1)
    os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
    raw=vs*0.6+os_*0.4
    af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
    score=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
    d['score']=score
    
    # Если есть accounts — считаем confidence factor
    if acc_df is not None and len(acc_df)>0:
        # Присоединяем accounts к d по времени (forward fill)
        d=d.join(acc_df, how='left').fillna(0)
        
        # phys_vol_per_account: fiz_net / fiz_accounts
        # Большой объём на один счёт = концентрация = сильный сигнал
        d['fiz_vol_pa']=d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
        # yur confidence: изменение числа счетов
        d['yur_a_change']=d['yur_buy_a']-d['yur_sell_a']
        d['yur_a_z']=rz(d['yur_a_change'],20)
        
        # concentration factor: 0..1
        # fiz_vol_pa > 1000 = крупный игрок (1.0), < 10 = толпа (0.0)
        d['conc']=np.clip(d['fiz_vol_pa']/1000.0,0,1)
        # yur_a_z > 2 = много новых юр-счетов = сильный сигнал
        d['yur_conf']=np.clip(d['yur_a_z']/2.0,0,1)
        
        # Модифицируем score: base * (1 + conc * 0.5 + yur_conf * 0.3)
        d['score_acc']=np.clip(d['score']*(1+d['conc']*0.5+d['yur_conf']*0.3),0,1)
    else:
        d['score_acc']=d['score']
    
    return d

def simulate(df, score_col, start, end, name="BASE"):
    cash=INITIAL_CAPITAL; peak=INITIAL_CAPITAL; max_dd=0
    trades=0; wins=0; filtered=0
    
    mask=(df.index >= pd.Timestamp(start)) & (df.index < pd.Timestamp(end))
    d=df[mask].copy()
    if len(d)==0:
        return {'name':name,'return_pct':0,'max_dd_pct':0,'calmar':0,'trades':0,'filtered':0}
    
    pos=None
    for i in range(1, len(d)):
        bar=d.iloc[i]
        ts=bar.name
        if hasattr(ts,'hour'): h=ts.hour
        else: h=pd.Timestamp(ts).hour
        if h<7 or h>=23: continue
        
        if pos is not None:
            pos['bars_left']-=1
            hit=False; ep=bar['close']; dr='time'
            if pos['dir']=='L' and bar['low']<=pos['stop']: hit=True; ep=pos['stop']; dr='stop'
            elif pos['dir']=='S' and bar['high']>=pos['stop']: hit=True; ep=pos['stop']; dr='stop'
            elif pos['bars_left']<=0: hit=True
            if hit:
                dm=1 if pos['dir']=='L' else -1
                pp=dm*(ep-pos['entry'])/pos['entry']
                pr=pp*pos['go']*pos['contracts']
                cash+=pr; trades+=1
                if pr>0: wins+=1
                pos=None
        
        if pos is not None:
            dm=1 if pos['dir']=='L' else -1
            mtm=dm*(bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq=cash+mtm
        else: teq=cash
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        if pos is not None: continue
        
        score=float(bar[score_col])
        if np.isnan(score) or score<0.25: continue
        
        go=TICKER_CONFIGS.get(SYMBOL,{}).get('go',5000)
        max_rub=cash*0.25
        contracts=max(1,int(max_rub/go))
        atrv=float(bar.get('atr14',1))
        ep=float(bar['close'])
        stop=ep-atrv*2
        pos={'dir':'L','entry':ep,'stop':stop,'bars_left':13,'go':go,'contracts':contracts}
    
    if pos is not None:
        lb=d.iloc[-1]
        dm=1 if pos['dir']=='L' else -1
        pp=dm*(lb['close']-pos['entry'])/pos['entry']
        pr=pp*pos['go']*pos['contracts']
        cash+=pr; trades+=1
        if pr>0: wins+=1
    
    tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    wr_=wins/trades*100 if trades>0 else 0
    days=(TEST_END-TEST_START).days
    years=max(days/365.25,0.1)
    cagr=((cash/INITIAL_CAPITAL)**(1/years)-1)*100 if cash>0 else -100
    calmar=tr/100/max(max_dd,0.001) if max_dd>0 else tr*10
    
    return {
        'name':name,'capital':round(cash,2),
        'return_pct':round(tr,2),'cagr_pct':round(cagr,2),
        'max_dd_pct':round(max_dd*100,2),'calmar':round(calmar,2),
        'wr_pct':round(wr_,2),'trades':trades,
    }


def main():
    print("Загрузка данных...")
    t0=time.time()
    df=load_data(SYMBOL)
    print(f"  {len(df)} баров OI за {time.time()-t0:.1f}s")
    
    print("Загрузка accounts (openinterest clgroup)...")
    t0=time.time()
    acc_df=load_accounts(SYMBOL)
    print(f"  {len(acc_df)} строк за {time.time()-t0:.1f}s")
    
    if len(acc_df)==0:
        print("⚠️ Нет accounts данных!")
        return
    
    print("Предвычисление сигналов...")
    t0=time.time()
    d=precompute(df, acc_df)
    print(f"  за {time.time()-t0:.1f}s")
    
    print(f"\nТест confidence factor на {SYMBOL}...\n")
    
    # BASE — без accounts
    r1=simulate(d,'score',TEST_START,TEST_END,name="BASE (без accounts)")
    print(f"  BASE: +{r1['return_pct']:.1f}%, DD={r1['max_dd_pct']:.1f}%, "
          f"Calmar={r1['calmar']:.1f}, сделок={r1['trades']}")
    
    # C confidence
    r2=simulate(d,'score_acc',TEST_START,TEST_END,name="C confidence")
    print(f"  +CONF: +{r2['return_pct']:.1f}%, DD={r2['max_dd_pct']:.1f}%, "
          f"Calmar={r2['calmar']:.1f}, сделок={r2['trades']}")
    
    print(f"\n{'='*70}")
    print(f"{'Вариант':<25} {'Return':>10} {'DD':>8} {'Calmar':>8} {'Сделок':>8}")
    print(f"{'='*70}")
    for r in [r1,r2]:
        print(f"{r['name']:<25} {r['return_pct']:>8.1f}% {r['max_dd_pct']:>6.1f}% {r['calmar']:>8.1f} {r['trades']:>8}")
    print(f"{'='*70}")
    
    if r2['calmar']>r1['calmar']:
        print(f"\n✅ Confidence factor улучшает: Calmar {r1['calmar']} → {r2['calmar']}")
    else:
        print(f"\n❌ Confidence factor НЕ улучшает")
    
    # Покажем распределение confidence
    print(f"\nРаспределение confidence в сигналах:")
    print(f"  Средний fz_conc: {d['conc'].mean():.2f}")
    print(f"  Средний yur_conf: {d['yur_conf'].mean():.2f}")
    print(f"  score: mean={d['score'].mean():.2f}, std={d['score'].std():.2f}")
    print(f"  score_acc: mean={d['score_acc'].mean():.2f}, std={d['score_acc'].std():.2f}")

if __name__=='__main__':
    main()
