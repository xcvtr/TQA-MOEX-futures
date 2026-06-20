#!/usr/bin/env python3
"""
Полный тест confidence factor на IS-честном портфеле (14 тикеров).
Сравнение: BASE (без accounts) vs +CONFIDENCE.
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


def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)


def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)


def get_ch():
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def load_oi_data(ch, symbols):
    """Загрузка prices_5m_oi + prices_5m"""
    data={}
    for sym in symbols:
        q=f"""
            SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
                   o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell,o.total_oi
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='2026-04-30'
            ORDER BY p.time
        """
        try:
            r=ch.query(q)
            if r.result_rows:
                cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']
                df=pd.DataFrame(r.result_rows,columns=cols)
                df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
                df.set_index('time',inplace=True)
                data[sym]=df
        except:
            pass
    return data


def load_accounts(ch, symbols):
    """Загрузка accounts из openinterest"""
    data={}
    for sym in symbols:
        q=f"""
            SELECT time, clgroup, buy_accounts, sell_accounts
            FROM moex.openinterest
            WHERE symbol='{sym}' AND time>='2024-01-01' AND time<='2026-04-30'
            ORDER BY time, clgroup
        """
        try:
            r=ch.query(q)
            rows=r.result_rows
            if not rows: continue
            recs=[]
            for row in rows:
                recs.append({'time':row[0],'clg':row[1],'buy_a':row[2],'sell_a':row[3]})
            df=pd.DataFrame(recs)
            df['time']=pd.to_datetime(df['time']).dt.tz_localize(None)
            fiz=df[df['clg']==0][['time','buy_a','sell_a']].rename(columns={'buy_a':'fiz_buy_a','sell_a':'fiz_sell_a'})
            yur=df[df['clg']==1][['time','buy_a','sell_a']].rename(columns={'buy_a':'yur_buy_a','sell_a':'yur_sell_a'})
            merged=pd.merge(fiz,yur,on='time',how='outer').fillna(0)
            merged.set_index('time',inplace=True)
            data[sym]=merged
        except:
            pass
    return data


def add_confidence(d, acc_df):
    """Добавление confidence factor к df"""
    d=d.join(acc_df, how='left').fillna(0)
    # concentration: объём на один счёт
    d['fiz_vol_pa']=d['fiz_net'].abs()/(d['fiz_buy_a']+d['fiz_sell_a']+1)
    d['yur_a_change']=d['yur_buy_a']-d['yur_sell_a']
    d['yur_a_z']=rz(d['yur_a_change'],20)
    d['conc']=np.clip(d['fiz_vol_pa']/1000.0,0,1)
    d['yur_conf']=np.clip(d['yur_a_z']/2.0,0,1)
    d['score_acc']=np.clip(d['score']*(1+d['conc']*0.5+d['yur_conf']*0.3),0,1)
    return d


def precompute(data, acc_data=None):
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
        sym_sigs={}; seen=set()
        
        # Если есть accounts — добавляем confidence
        has_acc = acc_data is not None and sym in acc_data
        
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
                
                # Если есть accounts — добавляем confidence score
                if has_acc:
                    dout2 = dout.copy()
                    dout2 = add_confidence(dout2, acc_data[sym])
                    sym_sigs[f"{k}_conf"] = (dout2, di, hold, atm)
                
                sym_sigs[k] = (dout, di, hold, atm)
        signals[sym] = sym_sigs
    return signals


def simulate(data, signals, start, end, kelly_min, kelly_max,
             use_conf=False, name="BASE"):
    cash=INITIAL_CAPITAL; peak=INITIAL_CAPITAL; max_dd=0
    kh=defaultdict(lambda:{'w':0,'l':0,'pnl':[]})
    pos={}; trades=[]
    
    all_ts=[]
    for sym in data:
        for t in data[sym].index:
            t_naive=t.to_pydatetime().replace(tzinfo=None)
            if start<=t_naive<=end:
                all_ts.append(t)
    all_ts=sorted(set(all_ts))
    
    for idx,ts in enumerate(all_ts):
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
                sk = sk + '_conf' if use_conf else sk
                if rs in signals and sk in signals[rs]:
                    dfs,_,_,_=signals[rs][sk]
                    if ts in dfs.index and float(dfs.loc[ts,'score'])<0.10:
                        ep=bar['close']; dr='fade'
            if ep is not None:
                dm=1 if p['dir']=='L' else -1
                pp=dm*(ep-p['entry'])/p['entry']
                pr=pp*p['go']*p['contracts']; cash+=pr
                trades.append({'sym':rs,'dir':p['dir'],'pnl_rub':pr,'reason':dr})
                if pr>0: kh[rs]['w']+=1
                else: kh[rs]['l']+=1
                kh[rs]['pnl'].append(pr)
                if len(kh[rs]['pnl'])>50: kh[rs]['pnl'].pop(0)
                to_close.append(sym)
        for s in to_close: del pos[s]
        
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
                sk = sk + '_conf' if use_conf else sk
                if sk not in signals[sym]: continue
                dfs,_,_,_=signals[sym][sk]
                if ts not in dfs.index: continue
                bs=dfs.loc[ts]
                score=float(bs.get('score_acc' if use_conf else 'score',0))
                if np.isnan(score) or score<(0.25 if di=='L' else 0.20): continue
                
                go=TICKER_CONFIGS.get(sym,{}).get('go',5000)
                k=kh[sym]
                kelly_v=kelly_min
                if k['w']+k['l']>=10:
                    wr_=k['w']/max(k['w']+k['l'],1)
                    aw=max(sum(p for p in k['pnl'] if p>0)/max(k['w'],1),1)
                    al=max(abs(sum(p for p in k['pnl'] if p<0)/max(k['l'],1)),1)
                    rr=aw/al if al>0 else 1.5
                    kv=wr_-(1-wr_)/max(rr,0.5)
                    kelly_v=max(kelly_min,min(kv,kelly_max))
                pct=min(kelly_v*score*w,0.35)
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
    else:
        days=0; years=0.1
    cagr=((cash/INITIAL_CAPITAL)**(1/years)-1)*100 if cash>0 else -100
    calmar=tr/100/max(max_dd,0.001) if max_dd>0 else tr*10
    
    return {
        'name': name,
        'capital': round(cash,2),
        'return_pct': round(tr,2),
        'cagr_pct': round(cagr,2),
        'max_dd_pct': round(max_dd*100,2),
        'calmar': round(calmar,2),
        'wr_pct': round(wr_,2),
        'trades': tt,
    }


def main():
    ch = get_ch()
    symbols = set()
    for lst in PORTFOLIO.values():
        for c in lst:
            symbols.add(c[0])
    symbols.add('SBERF')
    symbols.add('GLDRUBF')
    
    print(f"Загрузка {len(symbols)} тикеров...")
    t0=time.time()
    data = load_oi_data(ch, list(symbols))
    print(f"  {len(data)}/{len(symbols)} за {time.time()-t0:.1f}s")
    
    print("Загрузка accounts...")
    t0=time.time()
    acc_data = load_accounts(ch, list(symbols))
    print(f"  {len(acc_data)}/{len(symbols)} за {time.time()-t0:.1f}s")
    
    print("Предвычисление сигналов...")
    t0=time.time()
    signals = precompute(data, acc_data)
    print(f"  за {time.time()-t0:.1f}s")
    
    kelly_min, kelly_max = 0.03, 0.20
    
    print(f"\nЗапуск {TEST_START.date()} → {TEST_END.date()}...\n")
    
    # BASE
    r1 = simulate(data, signals, TEST_START, TEST_END, kelly_min, kelly_max,
                  use_conf=False, name="BASE")
    print(f"  BASE: +{r1['return_pct']:.1f}%, DD={r1['max_dd_pct']:.1f}%, "
          f"Calmar={r1['calmar']:.1f}, сделок={r1['trades']}")
    
    # +CONFIDENCE
    r2 = simulate(data, signals, TEST_START, TEST_END, kelly_min, kelly_max,
                  use_conf=True, name="+CONFIDENCE")
    print(f"  +CONF: +{r2['return_pct']:.1f}%, DD={r2['max_dd_pct']:.1f}%, "
          f"Calmar={r2['calmar']:.1f}, сделок={r2['trades']}")
    
    print(f"\n{'='*80}")
    print(f"{'Вариант':<25} {'Return':>10} {'CAGR':>10} {'DD':>8} {'Calmar':>8} {'Сделок':>8} {'WR':>6}")
    print(f"{'='*80}")
    for r in [r1, r2]:
        print(f"{r['name']:<25} {r['return_pct']:>8.1f}% {r['cagr_pct']:>8.1f}% "
              f"{r['max_dd_pct']:>6.1f}% {r['calmar']:>8.1f} {r['trades']:>8} {r['wr_pct']:>5.1f}%")
    print(f"{'='*80}")
    
    if r2['calmar'] > r1['calmar']:
        delta_calmar = (r2['calmar']/r1['calmar']-1)*100
        delta_dd = (r2['max_dd_pct']/max(r1['max_dd_pct'],0.1)-1)*100
        print(f"\n✅ Confidence улучшает: Calmar +{delta_calmar:.0f}%, DD {delta_dd:+.0f}%")
    else:
        print(f"\n❌ Confidence НЕ улучшает")
    
    out_dir = "reports/confidence_full_test"
    os.makedirs(out_dir, exist_ok=True)
    with open(f"{out_dir}/result.json", 'w') as f:
        json.dump([r1, r2], f, indent=2)
    print(f"\nРезультат: {out_dir}/result.json")


if __name__ == '__main__':
    main()
