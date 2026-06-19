#!/usr/bin/env python3
"""Quick market mode benchmark with fixed ct=1 — run only market mode."""
import sys, os, pickle, time, json
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from scripts.bar_level_sim import TICKER_CONFIGS

INITIAL_CAPITAL = 100_000
TEST_END = '2026-04-30'
SLIP = 0.0001
FIXED_CONTRACTS = 1

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

def rz(s,w=20):
    m=s.rolling(w,min_periods=w).mean();std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

all_symbols = sorted(set(c[0] for lst in PORTFOLIO.values() for c in lst))
print("Loading...", flush=True)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.tf_sweep_data.pkl'), 'rb') as f:
    data_5m = pickle.load(f)

test_start = datetime.strptime('2025-01-01','%Y-%m-%d')
test_end_dt = datetime.strptime(TEST_END,'%Y-%m-%d') + timedelta(days=1)
all_ts = sorted(set(t for sym,df in data_5m.items() for t in df.index
    if test_start <= (t.to_pydatetime().replace(tzinfo=None) if hasattr(t,'tz') and t.tz else t) <= test_end_dt))
print(f"{len(all_ts)} bars", flush=True)

print("Precomputing...", flush=True)
t0 = time.time()
signals = {}
for sym in all_symbols:
    if sym not in data_5m: continue
    d = data_5m[sym].copy()
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
print(f"Done {time.time()-t0:.0f}s", flush=True)

# Entry cache
entry_cache = {}
for sym in signals:
    for sk, (dfsig, di, hold, atm) in signals[sym].items():
        th = 0.25 if di == 'L' else 0.20
        mask = dfsig['score'] >= th
        entry_cache[(sym, sk)] = (dfsig[mask].copy(), di, hold, atm)

# Simulate market mode with fixed ct=1
cash = float(INITIAL_CAPITAL); peak = float(INITIAL_CAPITAL); max_dd = 0.0
positions = {}; all_trades = []; total_slip_cost = 0.0
pending = {}
np.random.seed(42)

for idx, ts in enumerate(all_ts):
    if idx % 10000 == 0 and idx > 0:
        locked = sum(p['go']*p.get('contracts',0) for p in positions.values())
        print(f"  {idx//10000+1}/7 cash={cash:.0f} locked={locked:.0f} pos={len(positions)} trades={len(all_trades)}", flush=True)
    
    for sym in list(pending.keys()):
        pend = pending.pop(sym)
        if sym not in data_5m or ts not in data_5m[sym].index: continue
        bar = data_5m[sym].loc[ts]
        ep = float(bar['close']) * (1+SLIP) if pend['di']=='L' else float(bar['close']) * (1-SLIP)
        stop = ep - pend['atr']*pend['atm'] if pend['di']=='L' else ep + pend['atr']*pend['atm']
        cost = pend['ct']*pend['go']
        if cost <= cash:
            cash -= cost
            total_slip_cost += cost * SLIP
            positions[sym] = {'dir':pend['di'],'hold':pend['hold'],'entry':ep,
                'stop':stop,'contracts':pend['ct'],'go':pend['go'],'bars_held':0}
    
    to_close = []
    for sym,pos in list(positions.items()):
        if sym not in data_5m or ts not in data_5m[sym].index: continue
        bar = data_5m[sym].loc[ts]
        ep = None
        if pos['dir']=='L' and bar['low']<=pos['stop']: ep=pos['stop']*(1-SLIP)
        elif pos['dir']=='S' and bar['high']>=pos['stop']: ep=pos['stop']*(1+SLIP)
        if ep is None and pos.get('bars_held',0) >= pos.get('hold',40): ep=bar['close']
        if ep is not None:
            dm=1 if pos['dir']=='L' else -1
            pr=dm*(ep-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
            cash+=pr; all_trades.append(pr)
            to_close.append(sym)
    for s in to_close: del positions[s]
    
    # MTM
    mtm=0
    for sym,pos in positions.items():
        if sym in data_5m and ts in data_5m[sym].index:
            bar=data_5m[sym].loc[ts]
            dm=1 if pos['dir']=='L' else -1
            mtm+=dm*(bar['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
    teq=cash+mtm
    if teq>peak: peak=teq
    ddv=(peak-teq)/peak if peak>0 else 0
    if ddv>max_dd: max_dd=ddv
    
    if ts.hour<7 or ts.hour>=23: continue
    locked=sum(p['go']*p.get('contracts',0) for p in positions.values())
    avail=cash-locked
    if avail<=0: continue
    
    entries=[]
    for lst_name,lst in PORTFOLIO.items():
        for sym,pat,di,hold,atm,w in lst:
            if sym in positions or sym in pending or sym not in data_5m: continue
            key = (sym, f"{pat}_{di}")
            if key not in entry_cache: continue
            df_ok, _, _, _ = entry_cache[key]
            if ts not in df_ok.index: continue
            bs = df_ok.loc[ts]
            score = float(bs['score'])
            if np.isnan(score): continue
            go = TICKER_CONFIGS.get(sym, {}).get('go', 5000)
            ct = FIXED_CONTRACTS
            atrv = float(bs.get('atr14', 0))
            if atrv==0 or np.isnan(atrv): continue
            ep_c = float(bs['close'])
            ep_c = ep_c * (1+SLIP) if di=='L' else ep_c * (1-SLIP)
            total_slip_cost += ct * go * SLIP
            stop = ep_c - atrv*atm if di=='L' else ep_c + atrv*atm
            entries.append((sym, ct, ep_c, stop, go, score))
    
    entries.sort(key=lambda e:e[5], reverse=True)
    for sym, ct, ep_c, stop, go, score in entries[:5]:
        cost = ct * go
        if cost > avail: continue
        positions[sym] = {'dir':'L','hold':hold,'entry':ep_c,'stop':stop,
            'contracts':ct,'go':go,'bars_held':0}
        avail -= cost

# Close remaining
for sym,pos in list(positions.items()):
    if sym in data_5m:
        lb = data_5m[sym].iloc[-1]
        dm = 1
        pr = dm*(lb['close']-pos['entry'])/pos['entry']*pos['go']*pos['contracts']
        cash+=pr; all_trades.append(pr)

tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
wins=sum(1 for p in all_trades if p>0)
total_t=len(all_trades)
days=max((all_ts[-1]-all_ts[0]).days,1)
years=max(days/365.25,0.1)
ann=(cash/INITIAL_CAPITAL)**(1/years)-1
cal=ann/max_dd if max_dd>0 else 0
tpd=total_t/days

print(f"\n{'='*60}")
print(f"MARKET MODE — fixed ct=1, threshold 0.25/0.20")
print(f"  Return: {tr:.1f}%")
print(f"  Ann: {ann*100:.1f}%")
print(f"  DD: {max_dd*100:.1f}%")
print(f"  Calmar: {cal:.2f}")
print(f"  Trades: {total_t} ({tpd:.1f}/day)")
print(f"  WR: {wins/total_t*100:.1f}%" if total_t else "  WR: N/A")
print(f"  Slip cost: {total_slip_cost:.0f}")
