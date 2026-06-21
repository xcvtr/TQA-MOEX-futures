#!/usr/bin/env python3
"""
Максимально быстрый тест MOEX-фильтров.
Берём 1 репрезентативный тикер (GL — золото, best performer).
Считаем 3 варианта: BASE / CLUSTER / HVN.
Кластеры: упрощённый алгоритм — rolling max за N баров.
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

# 1 тикер для быстрого теста
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
               o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
        FROM moex.prices_5m p
        LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
        WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='2026-04-30'
        ORDER BY p.time
    """
    r=ch.query(q)
    cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
    df=pd.DataFrame(r.result_rows,columns=cols)
    df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
    df.set_index('time',inplace=True)
    return df

def precompute(df):
    d=df.copy()
    d['volume']=d['volume'].astype(float)
    d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
    d['vr']=d['volume']/d['vma20'].clip(lower=1); d['vz']=rz(d['volume'],20)
    has_oi='fiz_buy' in d.columns
    if has_oi:
        d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0)
        d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
        d['fz']=rz(d['fiz_net'],20); d['yz']=rz(d['yur_net'],20)
        d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
        d['oima']=d['oi_r'].rolling(20).mean()
    d['atr14']=calc_atr(d); d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100
    
    # vod_L score (core config GL: vod, L, hold=13, atr=2)
    pat='vod'; di='L'; dm=1
    vs=np.clip((d['vr']-1.5)/3.0,0,1)
    if has_oi:
        os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
    else: os_=0.5
    raw=vs*0.6+os_*0.4
    af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
    score=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
    d['score']=score
    
    return d

def compute_clusters(df, lookback=20, vol_mult=3):
    """Предвычисление кластеров быстрейшим способом"""
    n=len(df)
    close=df['close'].values
    volume=df['volume'].values
    ca=np.full(n,np.nan)
    cb=np.full(n,np.nan)
    
    # Rolling: на каждом i ищем цену с макс объёмом в окне
    for i in range(lookback, n):
        w_c=close[max(0,i-lookback):i]
        w_v=volume[max(0,i-lookback):i]
        cp=close[i]
        
        # Упрощение: цена с макс объёмом = самый active level
        max_vol_price = w_c[np.argmax(w_v)]
        avg_vol = np.mean(w_v)
        if avg_vol == 0 or np.max(w_v) < avg_vol * vol_mult:
            continue
        
        if max_vol_price > cp:
            ca[i]=max_vol_price
        else:
            cb[i]=max_vol_price
    
    return ca, cb

def simulate(df, score_col, start, end, use_cluster=False, use_hvn=False,
             ca=None, cb=None, min_dist=0.003, name="BASE"):
    """
    Симуляция 1 тикера.  
    Просто: когда score > threshold → LONG, стоп ATR*2, hold=13.
    """
    # Отфильтровать по дате
    mask=(df.index >= pd.Timestamp(start)) & (df.index < pd.Timestamp(end))
    d=df[mask].copy()
    if len(d)==0:
        return {'name':name,'return_pct':0,'max_dd_pct':0,'calmar':0,'trades':0,'filtered':0}
    
    cash=INITIAL_CAPITAL
    peak=INITIAL_CAPITAL
    max_dd=0
    trades=0
    wins=0
    filtered=0
    
    pos=None  # {entry, stop, bars_left, go, contracts}
    
    for i in range(1, len(d)):
        bar=d.iloc[i]
        ts=bar.name
        if hasattr(ts,'hour'):
            h=ts.hour
        else:
            h=pd.Timestamp(ts).hour
        if h<7 or h>=23:
            continue
        
        # Выход
        if pos is not None:
            pos['bars_left']-=1
            hit=False; ep=bar['close']; dr='time'
            if pos['dir']=='L' and bar['low']<=pos['stop']:
                hit=True; ep=pos['stop']; dr='stop'
            elif pos['dir']=='S' and bar['high']>=pos['stop']:
                hit=True; ep=pos['stop']; dr='stop'
            elif pos['bars_left']<=0:
                hit=True
            
            if hit:
                dm=1 if pos['dir']=='L' else -1
                pp=dm*(ep-pos['entry'])/pos['entry']
                pr=pp*pos['go']*pos['contracts']
                cash+=pr
                trades+=1
                if pr>0: wins+=1
                pos=None
        
        # MTM
        if pos is not None:
            dm=1 if pos['dir']=='L' else -1
            mtm=dm*(bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            teq=cash+mtm
        else:
            teq=cash
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        
        if pos is not None:
            continue
        
        # Проверка сигнала
        score = float(bar[score_col])
        if np.isnan(score) or score < 0.25:
            continue
        
        # Фильтр кластера
        if use_cluster and ca is not None and cb is not None:
            idx=d.index.get_loc(ts)
            cp=float(bar['close'])
            nb=cb[idx] if not np.isnan(cb[idx]) else None
            na=ca[idx] if not np.isnan(ca[idx]) else None
            skip=False
            if nb is not None and (cp-nb)/cp < min_dist:
                skip=True
            if na is not None and (na-cp)/cp < min_dist:
                skip=True
            if skip:
                filtered+=1
                continue
        
        go=TICKER_CONFIGS.get(SYMBOL,{}).get('go',5000)
        max_rub=cash*0.25
        contracts=max(1,int(max_rub/go))
        atrv=float(bar.get('atr14',1))
        ep=float(bar['close'])
        stop=ep-atrv*2
        pos={'dir':'L','entry':ep,'stop':stop,'bars_left':13,'go':go,'contracts':contracts}
    
    # Закрытие
    if pos is not None:
        lb=d.iloc[-1]
        dm=1 if pos['dir']=='L' else -1
        pp=dm*(lb['close']-pos['entry'])/pos['entry']
        pr=pp*pos['go']*pos['contracts']
        cash+=pr
        trades+=1
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
        'wr_pct':round(wr_,2),'trades':trades,'filtered':filtered,
    }


def main():
    print("Загрузка данных...")
    t0=time.time()
    df=load_data(SYMBOL)
    print(f"  {len(df)} баров за {time.time()-t0:.1f}s")
    
    print("Предвычисление сигналов...")
    t0=time.time()
    d=precompute(df)
    print(f"  за {time.time()-t0:.1f}s")
    
    print("Предвычисление кластеров...")
    t0=time.time()
    ca, cb = compute_clusters(d)
    print(f"  за {time.time()-t0:.1f}s")
    
    print(f"\nТест на {SYMBOL} (2025-01-01 → 2026-05-01)...\n")
    
    results=[]
    
    # BASE
    r=simulate(d,'score',TEST_START,TEST_END,name="BASE")
    results.append(r)
    print(f"  BASE: +{r['return_pct']:.1f}%, DD={r['max_dd_pct']:.1f}%, "
          f"Calmar={r['calmar']:.1f}, сделок={r['trades']}")
    
    for dist in [0.002, 0.005, 0.01]:
        r=simulate(d,'score',TEST_START,TEST_END,use_cluster=True,
                   ca=ca,cb=cb,min_dist=dist,name=f"CLUSTER_{int(dist*1000)}")
        results.append(r)
        print(f"  CLUSTER {int(dist*1000)}: +{r['return_pct']:.1f}%, DD={r['max_dd_pct']:.1f}%, "
              f"Calmar={r['calmar']:.1f}, сделок={r['trades']}, filt={r['filtered']}")
    
    print("\n"+"="*90)
    print(f"{'Вариант':<20}{'Return':>10}{'DD':>8}{'Calmar':>10}{'Сделок':>8}{'Фильтр':>8}")
    print("="*90)
    for r in results:
        print(f"{r['name']:<20}{r['return_pct']:>8.1f}%{r['max_dd_pct']:>6.1f}%"
              f"{r['calmar']:>8.1f}{r['trades']:>8}{r.get('filtered',0):>8}")
    print("="*90)
    
    base=results[0]
    best=max(results[1:],key=lambda x:x['calmar'])
    print(f"\nЛучший: {best['name']} Calmar={best['calmar']}")
    if best['calmar']>base['calmar']:
        print(f"✅ УЛУЧШЕНИЕ: Calmar {base['calmar']} → {best['calmar']}")
    else:
        print(f"❌ Фильтры не улучшают BASE")

if __name__=='__main__':
    main()
