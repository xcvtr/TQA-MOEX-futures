#!/usr/bin/env python3
"""
Audit: modified walk-forward with CSV trade output.
Runs on short period 2025-01-01 to 2025-01-31.
Saves trades.csv with full field detail.
"""
import json, sys, os, random
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect
from bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000

PORTFOLIO = {
    'core': [
        ('GL','vod','L',21,2,1.0), ('RN','vou','L',5,5,1.0),
        ('AL','vou','L',21,2,1.0), ('HY','vou','L',5,5,1.0),
        ('NM','vod','L',21,3,1.0), ('AF','sm','L',21,2,1.0),
        ('SR','sm','L',8,5,1.0),   ('Si','vyf','L',13,2,1.0),
        ('SN','vou','L',5,5,1.0),  ('YD','vod','L',13,5,1.0),
    ],
    'hedge': [
        ('BR','vyf','S',13,5,1.0), ('SV','vod','S',5,5,1.0),
        ('SF','vod','S',8,3,1.0),  ('NG','vyf','S',5,5,1.0),
    ],
}

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def precompute_signals(data, symbols):
    signals = {}
    for sym in symbols:
        if sym not in data: continue
        d = data[sym].copy()
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
        sym_sigs={}
        seen=set()
        for lst in PORTFOLIO.values():
            for c in lst:
                sn,pat,di,hold,atm=c[0],c[1],c[2],c[3],c[4]
                if sn!=sym: continue
                k=f"{pat}_{di}"; 
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

def simulate_period(data, signals, start, end, kelly_min, kelly_max, label):
    cash = INITIAL_CAPITAL; peak = INITIAL_CAPITAL; max_dd = 0
    kelly_hist = defaultdict(lambda: {'w':0,'l':0,'pnl':[]})
    positions = {}; all_trades = []
    
    all_ts = []
    for sym in data:
        for t in data[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None) if hasattr(t, 'tz') and t.tz else t
            if isinstance(t_naive, pd.Timestamp):
                t_naive = t_naive.to_pydatetime().replace(tzinfo=None)
            start_n = start
            end_n = end
            if start_n <= t_naive <= end_n:
                all_ts.append(t)
    all_ts = sorted(set(all_ts))
    
    for idx, ts in enumerate(all_ts):
        # Выходы
        to_close = []
        for sym,pos in list(positions.items()):
            rs=pos.get('real_sym',sym)
            if rs not in data or ts not in data[rs].index: continue
            bar=data[rs].loc[ts]
            ep=None; r=''
            if pos['dir']=='L' and bar['low']<=pos['stop']: ep=pos['stop']; r='stop'
            elif pos['dir']=='S' and bar['high']>=pos['stop']: ep=pos['stop']; r='stop'
            if ep is None and pos.get('bars_held',0)>=pos.get('hold',40): ep=bar['close']; r='time'
            if ep is None and 'pattern' in pos:
                sk=f"{pos['pattern']}_{pos['dir']}"
                if rs in signals and sk in signals[rs]:
                    dfsig,_,_,_=signals[rs][sk]
                    if ts in dfsig.index and float(dfsig.loc[ts,'score'])<0.10:
                        ep=bar['close']; r='fade'
            if ep is not None:
                dm=1 if pos['dir']=='L' else -1
                pp=dm*(ep-pos['entry'])/pos['entry']
                pr=pp*pos['go']*pos['contracts']; cash+=pr
                all_trades.append({
                    'sym': rs,
                    'dir': pos['dir'],
                    'entry_time': str(pos['entry_ts']),
                    'entry_price': float(pos['entry']),
                    'exit_time': str(ts),
                    'exit_price': float(ep),
                    'pnl_rub': float(pr),
                    'reason': r,
                    'contracts': int(pos['contracts']),
                    'go': float(pos['go']),
                    'stop': float(pos['stop']),
                    'pattern': pos.get('pattern','?'),
                })
                if pr>0: kelly_hist[rs]['w']+=1
                else: kelly_hist[rs]['l']+=1
                kelly_hist[rs]['pnl'].append(pr)
                if len(kelly_hist[rs]['pnl'])>50: kelly_hist[rs]['pnl'].pop(0)
                to_close.append(sym)
        for s in to_close: del positions[s]
        
        # MTM
        mtm=0
        for sym,pos in list(positions.items()):
            rs=pos.get('real_sym',sym)
            if rs in data and ts in data[rs].index:
                bar=data[rs].loc[ts]; dm=1 if pos['dir']=='L' else -1
                mtm+=dm*(bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
        teq=cash+mtm
        if teq>peak: peak=teq
        ddv=(peak-teq)/peak if peak>0 else 0
        if ddv>max_dd: max_dd=ddv
        
        # Входы (если не слишком поздно)
        if ts.hour<7 or ts.hour>=23: continue
        locked=sum(p['go']*p.get('contracts',0) for p in positions.values())
        avail=cash-locked
        if avail<=0: continue
        
        entries=[]
        for lst_name,lst in PORTFOLIO.items():
            for sym,pat,di,hold,atm,w in lst:
                if sym in positions or sym not in data: continue
                if sym not in signals: continue
                sk=f"{pat}_{di}"; 
                if sk not in signals[sym]: continue
                dfsig,_,_,_=signals[sym][sk]
                if ts not in dfsig.index: continue
                bs=dfsig.loc[ts]
                score=float(bs.get('score',0))
                if np.isnan(score) or score<(0.25 if di=='L' else 0.20): continue
                go=TICKER_CONFIGS.get(sym,{}).get('go',5000)
                kh=kelly_hist[sym]
                kelly=kelly_min
                if kh['w']+kh['l']>=10:
                    wr_=kh['w']/max(kh['w']+kh['l'],1)
                    aw=max(sum(p for p in kh['pnl'] if p>0)/max(kh['w'],1),1)
                    al=max(abs(sum(p for p in kh['pnl'] if p<0)/max(kh['l'],1)),1)
                    rr=aw/al if al>0 else 1.5
                    k=wr_-(1-wr_)/max(rr,0.5)
                    kelly=max(kelly_min,min(k,kelly_max))
                pct=min(kelly*score*w,0.35)
                mr=avail*pct
                ct=max(1,int(mr/go))
                if ct==0: continue
                atrv=float(bs.get('atr14',0))
                if atrv==0 or np.isnan(atrv): continue
                ep=float(bs['close'])
                stop=ep-atrv*atm if di=='L' else ep+atrv*atm
                entries.append((sym,pat,di,hold,ct,ep,stop,go,score,lst_name))
        entries.sort(key=lambda e:e[8],reverse=True)
        for ent in entries[:5]:
            sym,pat,di,hold,ct,ep,stop,go,score,role=ent
            cost=ct*go
            if cost>avail: continue
            positions[sym]={'real_sym':sym,'dir':di,'hold':hold,'entry':ep,'stop':stop,'contracts':ct,'go':go,'bars_held':0,'entry_ts':ts,'pattern':pat}
            avail-=cost
    
    # Закрытие остатков
    for sym,pos in list(positions.items()):
        rs=pos.get('real_sym',sym)
        if rs in data:
            lb=data[rs].iloc[-1]
            dm=1 if pos['dir']=='L' else -1
            pp=dm*(lb['close']-pos['entry'])/pos['entry']
            pr=pp*pos['go']*pos['contracts']; cash+=pr
            all_trades.append({
                'sym': rs,
                'dir': pos['dir'],
                'entry_time': str(pos['entry_ts']),
                'entry_price': float(pos['entry']),
                'exit_time': str(data[rs].index[-1]),
                'exit_price': float(lb['close']),
                'pnl_rub': float(pr),
                'reason': 'eod',
                'contracts': int(pos['contracts']),
                'go': float(pos['go']),
                'stop': float(pos['stop']),
                'pattern': pos.get('pattern','?'),
            })
    
    tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    wins=sum(1 for t in all_trades if t.get('pnl_rub',0)>0)
    total_t=len(all_trades)
    wr_=wins/total_t*100 if total_t>0 else 0
    if all_ts:
        days=(all_ts[-1]-all_ts[0]).days
        years=max(days/365.25,0.1)
    else:
        days=0; years=0.1
    ann=(cash/INITIAL_CAPITAL)**(1/max(years,0.1))-1
    cal=ann/(max_dd) if max_dd>0 else 0
    
    sym_stats=defaultdict(lambda:{'pnl':0,'w':0,'l':0,'n':0})
    for t in all_trades:
        s=t.get('sym','?')
        sym_stats[s]['pnl']+=t.get('pnl_rub',0)
        sym_stats[s]['n']+=1
        if t.get('pnl_rub',0)>0: sym_stats[s]['w']+=1
    
    print(f"\n{'='*50}")
    print(f"{label}")
    print(f"{'='*50}")
    print(f"Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽")
    print(f"Return:  {tr:+.1f}%  ({ann*100:+.1f}%/год)")
    print(f"Max DD:  {max_dd*100:.1f}%")
    print(f"Calmar:  {cal:.2f}")
    print(f"WR:      {wr_:.1f}% ({wins}/{total_t})")
    print(f"Период:  {days} дней" if all_ts else "")
    
    for s,st in sorted(sym_stats.items(),key=lambda x:x[1]['pnl'],reverse=True):
        ws=st['w']/st['n']*100 if st['n']>0 else 0
        print(f"  {s}: {st['pnl']:+,.0f} ₽ WR={ws:.0f}% ({st['n']} тр)")
    
    return {'capital':cash,'return_pct':tr,'annual_return':ann*100,'max_dd_pct':max_dd*100,'calmar':cal,'wr':wr_,'n_trades':total_t}, all_trades


if __name__ == '__main__':
    all_symbols = set()
    for lst in PORTFOLIO.values(): all_symbols.update(c[0] for c in lst)
    print(f"=== Walk-Forward Audit (short period) ===")
    print(f"Тикеры: {sorted(all_symbols)}")
    
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    print("\nЗагрузка данных (2024-01 до 2025-01-31)...")
    data_all = {}
    for sym in all_symbols:
        q = f"""
            SELECT p.time,p.open,p.high,p.low,p.close,p.volume,
                   o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='2025-01-31'
            ORDER BY p.time
        """
        try:
            r = ch.query(q)
            if r.result_rows:
                cols=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
                df=pd.DataFrame(r.result_rows,columns=cols)
                df['time']=pd.to_datetime(df['time']); df.set_index('time',inplace=True)
                data_all[sym]=df
                print(f"  ✓ {sym}: {len(df)} bars")
        except Exception as e:
            print(f"  ✗ {sym}: {e}")
    
    print("\nПредвычисление сигналов...")
    signals_all = precompute_signals(data_all, list(all_symbols))
    print(f"Сигналы: {len(signals_all)} тикеров")
    
    test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
    test_end_dt = datetime.strptime('2025-01-31', '%Y-%m-%d') + timedelta(days=1)
    
    result, all_trades = simulate_period(
        data_all, signals_all, test_start, test_end_dt,
        kelly_min=0.40, kelly_max=1.50, label="TEST Jan 2025 (OOS, Kelly 40-150%)"
    )
    
    print(f"\nВсего сделок: {len(all_trades)}")
    
    # Save CSV
    out_dir = 'reports/phase5_walkforward'
    os.makedirs(out_dir, exist_ok=True)
    
    csv_path = os.path.join(out_dir, 'audit_trades.csv')
    import csv
    fieldnames = ['sym','dir','entry_time','entry_price','exit_time','exit_price','pnl_rub','reason','contracts','go','stop','pattern']
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in all_trades:
            writer.writerow(t)
    print(f"CSV сохранён: {csv_path}")
    
    # Also save JSON for easy reading
    json_path = os.path.join(out_dir, 'audit_trades.json')
    with open(json_path, 'w') as f:
        json.dump(all_trades, f, indent=2, ensure_ascii=False, default=str)
    print(f"JSON сохранён: {json_path}")
    
    print("\nDone.")
