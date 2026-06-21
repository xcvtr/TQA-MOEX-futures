#!/usr/bin/env python3
"""
Быстрый тест MOEX-фильтров — предвычисление кластеров.
V2: вся арифметика векторизована.
"""
import sys, os, json
from datetime import datetime
from collections import defaultdict

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
                data[sym]=df
        except:
            pass
    return data

def precompute_clusters(df, lookback=20, vol_mult=3, vol_percentile=70):
    """Предвычисление кластеров ВЕКТОРИЗОВАННО для всех баров сразу"""
    n = len(df)
    close = df['close'].values.astype(np.float64)
    volume = df['volume'].values.astype(np.float64)
    
    # Для каждого бара считаем кластеры
    cluster_above = np.full(n, np.nan)
    cluster_below = np.full(n, np.nan)
    hvn_above = np.full(n, np.nan)
    hvn_below = np.full(n, np.nan)
    
    for i in range(lookback, n):
        w_close = close[max(0,i-lookback):i]
        w_vol = volume[max(0,i-lookback):i]
        cp = close[i]
        
        step = max(cp * 0.001, 0.01)
        
        # Группировка: округление close до step
        bins = np.round(w_close / step) * step
        # Сумма объёма по ценам
        unique_bins = np.unique(bins)
        vol_by_price = {b: 0.0 for b in unique_bins}
        for j, b in enumerate(bins):
            vol_by_price[b] += w_vol[j]
        
        if not vol_by_price:
            continue
        
        vols = np.array(list(vol_by_price.values()))
        prices = np.array(list(vol_by_price.keys()))
        avg_vol = np.mean(vols)
        
        if avg_vol == 0:
            continue
        
        # Pseudo-DOM кластеры: > vol_mult * avg
        mask = vols > avg_vol * vol_mult
        cluster_prices = prices[mask]
        if len(cluster_prices) > 0:
            above = cluster_prices[cluster_prices > cp]
            below = cluster_prices[cluster_prices < cp]
            if len(above) > 0:
                cluster_above[i] = np.min(above)
            if len(below) > 0:
                cluster_below[i] = np.max(below)
        
        # HVN: top vol_percentile
        threshold = np.percentile(vols, vol_percentile)
        hvn_mask = vols >= threshold
        hvn_prices = prices[hvn_mask]
        if len(hvn_prices) > 0:
            above = hvn_prices[hvn_prices > cp]
            below = hvn_prices[hvn_prices < cp]
            if len(above) > 0:
                hvn_above[i] = np.min(above)
            if len(below) > 0:
                hvn_below[i] = np.max(below)
    
    return cluster_above, cluster_below, hvn_above, hvn_below


def precompute(data):
    """Предвычисление сигналов + кластеров"""
    signals = {}
    
    for sym, df in data.items():
        d = df.copy()
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


def simulate(data, signals, start, end, kelly_min, kelly_max,
             use_cluster=False, use_hvn=False, min_dist=0.002,
             cluster_above=None, cluster_below=None,
             hvn_above=None, hvn_below=None,
             name="BASE"):
    cash=INITIAL_CAPITAL; peak=INITIAL_CAPITAL; max_dd=0
    kh=defaultdict(lambda:{'w':0,'l':0,'pnl':[]})
    pos={}; trades=[]
    filtered=0
    
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
                if sk not in signals[sym]: continue
                dfs,_,_,_=signals[sym][sk]
                if ts not in dfs.index: continue
                bs=dfs.loc[ts]
                score=float(bs.get('score',0))
                if np.isnan(score) or score<(0.25 if di=='L' else 0.20): continue
                
                # Фильтр кластеров
                bar_idx = dfs.index.get_loc(ts)
                if use_cluster and cluster_below is not None and cluster_above is not None:
                    cp = float(bs['close'])
                    if di == 'L':
                        nb = cluster_below[sym][bar_idx]
                        if not np.isnan(nb):
                            dist = (cp - nb) / cp
                            if dist < min_dist:
                                filtered += 1
                                continue
                    elif di == 'S':
                        na = cluster_above[sym][bar_idx]
                        if not np.isnan(na):
                            dist = (na - cp) / cp
                            if dist < min_dist:
                                filtered += 1
                                continue
                
                if use_hvn and hvn_below is not None and hvn_above is not None:
                    cp = float(bs['close'])
                    if di == 'L':
                        nb = hvn_below[sym][bar_idx]
                        if not np.isnan(nb):
                            dist = (cp - nb) / cp
                            if dist < min_dist:
                                filtered += 1
                                continue
                    elif di == 'S':
                        na = hvn_above[sym][bar_idx]
                        if not np.isnan(na):
                            dist = (na - cp) / cp
                            if dist < min_dist:
                                filtered += 1
                                continue
                
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
        'filtered': filtered,
    }


def main():
    ch = get_ch()
    symbols = set()
    for lst in PORTFOLIO.values():
        for c in lst:
            symbols.add(c[0])
    symbols.add('GLDRUBF')
    symbols.add('SBERF')
    
    print(f"Загрузка {len(symbols)} тикеров...")
    data = load_data(ch, list(symbols))
    print(f"Загружено: {len(data)}/{len(symbols)}")
    
    print("Предвычисление сигналов...")
    signals = precompute(data)
    
    print("Предвычисление кластеров (pseudo-DOM + HVN)...")
    # Предвычисляем cluster_above/below и hvn_above/below для каждого тикера
    cluster_data = {}
    for sym, df in data.items():
        ca, cb, ha, hb = precompute_clusters(df)
        cluster_data[sym] = (ca, cb, ha, hb)
    
    cluster_above = {sym: cluster_data[sym][0] for sym in cluster_data}
    cluster_below = {sym: cluster_data[sym][1] for sym in cluster_data}
    hvn_above = {sym: cluster_data[sym][2] for sym in cluster_data}
    hvn_below = {sym: cluster_data[sym][3] for sym in cluster_data}
    
    kelly_min, kelly_max = 0.03, 0.20
    
    # 3 дистанции: strict (0.2%), default (0.5%), wide (1.0%)
    distances = [0.002, 0.005, 0.01]
    
    print("\nЗапуск...")
    results = []
    
    # BASE
    r = simulate(data, signals, TEST_START, TEST_END, kelly_min, kelly_max,
                 name="BASE")
    results.append(r)
    print(f"  BASE: +{r['return_pct']:.1f}%, DD={r['max_dd_pct']:.1f}%, "
          f"Calmar={r['calmar']:.1f}, сделок={r['trades']}")
    
    for d in distances:
        r = simulate(data, signals, TEST_START, TEST_END, kelly_min, kelly_max,
                     use_cluster=True, use_hvn=False, min_dist=d,
                     cluster_above=cluster_above, cluster_below=cluster_below,
                     name=f"CLUSTER_{int(d*1000)}")
        results.append(r)
        print(f"  CLUSTER {int(d*1000)}: +{r['return_pct']:.1f}%, DD={r['max_dd_pct']:.1f}%, "
              f"Calmar={r['calmar']:.1f}, сделок={r['trades']}, отсечено={r['filtered']}")
    
    for d in distances:
        r = simulate(data, signals, TEST_START, TEST_END, kelly_min, kelly_max,
                     use_cluster=False, use_hvn=True, min_dist=d,
                     hvn_above=hvn_above, hvn_below=hvn_below,
                     name=f"HVN_{int(d*1000)}")
        results.append(r)
        print(f"  HVN {int(d*1000)}: +{r['return_pct']:.1f}%, DD={r['max_dd_pct']:.1f}%, "
              f"Calmar={r['calmar']:.1f}, сделок={r['trades']}, отсечено={r['filtered']}")
    
    for d in distances:
        r = simulate(data, signals, TEST_START, TEST_END, kelly_min, kelly_max,
                     use_cluster=True, use_hvn=True, min_dist=d,
                     cluster_above=cluster_above, cluster_below=cluster_below,
                     hvn_above=hvn_above, hvn_below=hvn_below,
                     name=f"ALL_{int(d*1000)}")
        results.append(r)
        print(f"  ALL {int(d*1000)}: +{r['return_pct']:.1f}%, DD={r['max_dd_pct']:.1f}%, "
              f"Calmar={r['calmar']:.1f}, сделок={r['trades']}, отсечено={r['filtered']}")
    
    print("\n" + "="*100)
    print(f"{'Вариант':<20} {'Return':>10} {'CAGR':>10} {'DD':>8} {'Calmar':>8} {'Сделок':>8} {'Отсечено':>10}")
    print("="*100)
    for r in results:
        print(f"{r['name']:<20} {r['return_pct']:>8.1f}% {r['cagr_pct']:>8.1f}% "
              f"{r['max_dd_pct']:>6.1f}% {r['calmar']:>8.1f} {r['trades']:>8} {r.get('filtered',0):>10}")
    print("="*100)
    
    base_r = results[0]['return_pct']
    best = max(results[1:], key=lambda r: r['calmar'])
    print(f"\nЛучший: {best['name']} (Calmar={best['calmar']})")
    if best['calmar'] > results[0]['calmar']:
        print(f"✅ Улучшение: Calmar {results[0]['calmar']} → {best['calmar']}")
    else:
        print(f"❌ Фильтры не улучшают. BASE остаётся лучшим.")
    
    out_dir = "reports/cluster_filters_test"
    os.makedirs(out_dir, exist_ok=True)
    with open(f"{out_dir}/results.json", 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nРезультат: {out_dir}/results.json")


if __name__ == '__main__':
    main()
