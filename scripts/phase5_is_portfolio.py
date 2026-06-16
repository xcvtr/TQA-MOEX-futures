#!/usr/bin/env python3
"""
Walk-forward с портфелем, отобранным ТОЛЬКО по IS Calmar.
Никакого подсмотра в OOS при отборе тикеров/параметров.
"""
import json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000

# Портфель отобран ТОЛЬКО по IS Calmar
PORTFOLIO = {
    'core': [
        ('GL','vod','L',13,2,1.0), ('MM','sm','L',21,2,1.0),
        ('HY','vyf','L',8,3,1.0),  ('NM','sm','L',21,3,1.0),
        ('YD','vod','L',21,5,1.0), ('NG','vou','L',5,5,1.0),
        ('AL','sm','L',21,2,1.0),  ('AF','vod','L',21,2,1.0),
        ('PT','vod','L',21,3,1.0), ('RN','vou','L',13,2,1.0),
    ],
    'hedge': [
        ('SV','sm','S',5,5,1.0),   ('GLDRUBF','vyf','S',5,5,1.0),
        ('VB','vou','S',5,5,1.0),  ('SBERF','sm','S',21,2,1.0),
    ],
}

TEST_START = datetime(2025, 1, 1)
TEST_END = datetime(2026, 5, 1)

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def load_data(ch, symbols):
    data={}
    for sym in symbols:
        q=f"""
            SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
                   o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='2026-04-30'
            ORDER BY p.time
        """
        try:
            r=ch.query(q)
            if r.result_rows:
                cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
                df=pd.DataFrame(r.result_rows,columns=cols)
                df['time']=pd.to_datetime(df['time']); df.set_index('time',inplace=True)
                data[sym]=df; print(f"  ✓ {sym}: {len(df)} bars")
        except Exception as e: print(f"  ✗ {sym}: {e}")
    return data

def precompute(data):
    signals={}
    for sym,df in data.items():
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
        sym_sigs={}; seen=set()
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
                    if has_oi:
                        os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1) if pat=='vod' else np.clip((d['oi_r']-d['oima'])/d['oima'].clip(lower=0.1),0,1)
                    else: os_=0.5
                    raw=vs*0.6+os_*0.4
                elif pat=='sm':
                    if has_oi: raw=np.clip(abs(d['yz'])/3.0,0,1)*0.7+np.clip(abs(d['fz'])/3.0,0,1)*0.3
                    else: raw=np.clip((d['vr']-1.5)/3.0,0,1)
                elif pat=='vyf':
                    vs=np.clip((d['vr']-2.0)/4.0,0,1)
                    if has_oi: ys=np.clip(d['yur_net'].fillna(0)/max(d['yur_net'].std(),1)*dm,0,1)
                    else: ys=np.clip((d['close']-d['close'].shift(1))/d['close'].shift(1).clip(lower=1)*50,0,1)
                    raw=vs*0.5+ys*0.5
                else: raw=np.clip((d['vr']-2.5)/5.0,0,1)
                af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
                score=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)
                dout=d.copy(); dout['score']=score
                sym_sigs[k]=(dout,di,hold,atm)
        signals[sym]=sym_sigs
    return signals

def simulate(data, signals, start, end, kelly_min, kelly_max, label, monthly_pnl=None):
    cash=INITIAL_CAPITAL; peak=INITIAL_CAPITAL; max_dd=0
    if monthly_pnl is None: monthly_pnl={}  # будет как defaultdict(float)
    kh=defaultdict(lambda:{'w':0,'l':0,'pnl':[]})
    pos={}; trades=[]
    
    # Собираем временные метки в диапазоне
    all_ts=[]
    for sym in data:
        for t in data[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None)
            if start <= t_naive <= end:
                all_ts.append(t)
    all_ts=sorted(set(all_ts))
    
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
                    if ts in dfs.index and float(dfs.loc[ts,'score'])<0.10:
                        ep=bar['close']; dr='fade'
            if ep is not None:
                dm=1 if p['dir']=='L' else -1
                pp=dm*(ep-p['entry'])/p['entry']
                pr=pp*p['go']*p['contracts']; cash+=pr
                trades.append({'sym':rs,'dir':p['dir'],'pnl_rub':pr,'reason':dr})
                if monthly_pnl is not None:
                    monthly_pnl[ts.strftime('%Y-%m')] = monthly_pnl.get(ts.strftime('%Y-%m'), 0) + pr
                if pr>0: kh[rs]['w']+=1
                else: kh[rs]['l']+=1
                kh[rs]['pnl'].append(pr)
                if len(kh[rs]['pnl'])>50: kh[rs]['pnl'].pop(0)
                to_close.append(sym)
        for s in to_close: del pos[s]
        
        # MTM
        mtm=0
        for sym,p in list(pos.items()):
            rs=p.get('real_sym',sym)
            if rs in data and ts in data[rs].index:
                bar=data[rs].loc[ts]; dm=1 if p['dir']=='L' else -1
                mtm+=dm*(bar['close']-p['entry'])/p['entry']*p['go']*p['contracts']
        teq=cash+mtm
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        
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
                kelly=kelly_min
                if k['w']+k['l']>=10:
                    wr_=k['w']/max(k['w']+k['l'],1)
                    aw=max(sum(p for p in k['pnl'] if p>0)/max(k['w'],1),1)
                    al=max(abs(sum(p for p in k['pnl'] if p<0)/max(k['l'],1)),1)
                    rr=aw/al if al>0 else 1.5
                    kv=wr_-(1-wr_)/max(rr,0.5)
                    kelly=max(kelly_min,min(kv,kelly_max))
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
    
    # Закрытие остатков
    for sym,p in list(pos.items()):
        rs=p.get('real_sym',sym)
        if rs in data:
            lb=data[rs].iloc[-1]
            dm=1 if p['dir']=='L' else -1
            pp=dm*(lb['close']-p['entry'])/p['entry']
            pr=pp*p['go']*p['contracts']; cash+=pr
            trades.append({'sym':rs,'dir':p['dir'],'pnl_rub':pr,'reason':'eod'})
    
    tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    wins=sum(1 for t in trades if t.get('pnl_rub',0)>0)
    tt=len(trades)
    wr_=wins/tt*100 if tt>0 else 0
    if all_ts:
        days=(all_ts[-1]-all_ts[0]).days
        years=max(days/365.25,0.1)
    ann=(cash/INITIAL_CAPITAL)**(1/max(years,0.1))-1
    cal=ann/(max_dd) if max_dd>0 else 0
    
    sym_st=defaultdict(lambda:{'pnl':0,'w':0,'l':0,'n':0})
    for t in trades:
        s=t.get('sym','?')
        sym_st[s]['pnl']+=t.get('pnl_rub',0); sym_st[s]['n']+=1
        if t.get('pnl_rub',0)>0: sym_st[s]['w']+=1
    
    print(f"\n{'='*50}")
    print(f"{label}")
    print(f"{'='*50}")
    print(f"Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽")
    print(f"Return:  {tr:+.1f}%  ({ann*100:+.1f}%/год)")
    print(f"Max DD:  {max_dd*100:.1f}%")
    print(f"Calmar:  {cal:.2f}")
    print(f"WR:      {wr_:.1f}% ({wins}/{tt})")
    
    for s,st in sorted(sym_st.items(),key=lambda x:x[1]['pnl'],reverse=True):
        ws=st['w']/st['n']*100 if st['n']>0 else 0
        print(f"  {s}: {st['pnl']:+,.0f} ₽ WR={ws:.0f}% ({st['n']} тр)")
    
    return {'capital':cash,'return_pct':tr,'annual_return':ann*100,'max_dd_pct':max_dd*100,'calmar':cal,'wr':wr_,'n_trades':tt}


if __name__ == '__main__':
    symbols=set()
    for lst in PORTFOLIO.values(): symbols.update(c[0] for c in lst)
    print(f"=== Walk-forward IS-based portfolio (no OOS peek) ===")
    print(f"Тикеры: {sorted(symbols)}")
    print(f"Test period: {TEST_START.date()} - {TEST_END.date()}")
    print(f"IS Calmar отбор (без подсмотра в OOS)")
    
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    print("Loading data...")
    data = load_data(ch, list(symbols))
    
    print("Precomputing signals...")
    signals = precompute(data)
    
    print(f"{'#'*60}")
    print("TEST 2025-2026 (OOS, чистый — портфель отобран по IS)")
    print(f"{'#'*60}")
    monthly_pnl={}
    r = simulate(data, signals, TEST_START, TEST_END, 0.40, 1.50, "IS-Portfolio OOS 2025-2026", monthly_pnl)
    
    print(f"\n{'#'*60}")
    print("MONTHLY PnL")
    print(f"{'#'*60}")
    for m in sorted(monthly_pnl.keys()):
        pnl=monthly_pnl[m]
        print(f"  {m}: {pnl:+,.0f} ₽")
    
    # (пропускаем второй тест для скорости)
    os.makedirs('reports/phase5_is_portfolio', exist_ok=True)
    with open('reports/phase5_is_portfolio/result.json','w') as f:
        json.dump({'kelly_40_150':r,'monthly_pnl':monthly_pnl}, f, indent=2, ensure_ascii=False)
    
    print(f"\nSaved: reports/phase5_is_portfolio/result.json")
