#!/usr/bin/env python3
"""
TF sweep для Phase 5 портфеля.
Тестируем 3 таймфрейма с одинаковым портфелем и параметрами:
- 5m (оригинал)
- 15m
- H1

Для каждого: количество сделок/день, return, DD, Calmar.
Без комиссий — честное сравнение TF.
"""

import sys, os, json, time
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bar_level_sim import TICKER_CONFIGS
except ImportError:
    TICKER_CONFIGS = {}

INITIAL_CAPITAL = 100_000
TEST_END = '2026-04-30'

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

RULE_MAP = {'5m': '5min', '15m': '15min', 'H1': '1h'}

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def resample_to_tf(df, rule):
    """Resample 5m OHLCV+OI to target TF."""
    agg = {
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
        'fiz_buy': 'last', 'fiz_sell': 'last', 'yur_buy': 'last', 'yur_sell': 'last',
    }
    return df.resample(rule).agg(agg).dropna(subset=['close'])

def precompute_signals_tf(data, symbols, tf_rule):
    signals = {}
    for sym in symbols:
        if sym not in data: continue
        d = data[sym].copy()
        if tf_rule != '5min':
            d = resample_to_tf(d, tf_rule)
        if len(d) < 50: continue

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

def simulate_period(data, signals, start, end, kelly_min, kelly_max, label, tf_rule):
    cash = INITIAL_CAPITAL; peak = INITIAL_CAPITAL; max_dd = 0
    kelly_hist = defaultdict(lambda: {'w':0,'l':0,'pnl':[]})
    positions = {}; all_trades = []

    # Определяем часы работы для TF
    # 5m: 7-23, 15m/H1: весь день (9:45-18:45 МСК ≈ 7-23 IRKT)
    if tf_rule == '5min':
        hours_ok = lambda ts: 7 <= ts.hour < 23
    else:
        hours_ok = lambda ts: 7 <= ts.hour < 23

    all_ts = []
    for sym in data:
        for t in data[sym].index:
            t_naive = t.to_pydatetime().replace(tzinfo=None) if hasattr(t, 'tz') and t.tz else t
            if isinstance(t_naive, pd.Timestamp):
                t_naive = t_naive.to_pydatetime().replace(tzinfo=None)
            if start <= t_naive <= end:
                all_ts.append(t)
    all_ts = sorted(set(all_ts))

    for idx, ts in enumerate(all_ts):
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
                all_trades.append({'sym':rs,'dir':pos['dir'],'pnl_rub':pr,'reason':r})
                if pr>0: kelly_hist[rs]['w']+=1
                else: kelly_hist[rs]['l']+=1
                kelly_hist[rs]['pnl'].append(pr)
                if len(kelly_hist[rs]['pnl'])>50: kelly_hist[rs]['pnl'].pop(0)
                to_close.append(sym)
        for s in to_close: del positions[s]

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

        if not hours_ok(ts): continue
        locked=sum(p['go']*p.get('contracts',0) for p in positions.values())
        avail=cash-locked
        if avail<=0: continue

        entries=[]
        for lst_name,lst in PORTFOLIO.items():
            for sym,pat,di,hold,atm,w in lst:
                if sym in positions or sym not in data: continue
                if sym not in signals: continue
                sk=f"{pat}_{di}"
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
                ep_c=float(bs['close'])
                stop=ep_c-atrv*atm if di=='L' else ep_c+atrv*atm
                entries.append((sym,pat,di,hold,ct,ep_c,stop,go,score,lst_name))
        entries.sort(key=lambda e:e[8],reverse=True)
        for ent in entries[:5]:
            sym,pat,di,hold,ct,ep_c,stop,go,score,role=ent
            cost=ct*go
            if cost>avail: continue
            positions[sym]={'real_sym':sym,'dir':di,'hold':hold,'entry':ep_c,'stop':stop,'contracts':ct,'go':go,'bars_held':0,'entry_ts':ts,'pattern':pat}
            avail-=cost

    for sym,pos in list(positions.items()):
        rs=pos.get('real_sym',sym)
        if rs in data:
            lb=data[rs].iloc[-1]
            dm=1 if pos['dir']=='L' else -1
            pp=dm*(lb['close']-pos['entry'])/pos['entry']
            pr=pp*pos['go']*pos['contracts']; cash+=pr
            all_trades.append({'sym':rs,'dir':pos['dir'],'pnl_rub':pr,'reason':'eod'})

    tr=(cash-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    wins=sum(1 for t in all_trades if t.get('pnl_rub',0)>0)
    total_t=len(all_trades)
    if all_ts:
        days=max((all_ts[-1]-all_ts[0]).days, 1)
        years=max(days/365.25, 0.1)
    ann=(cash/INITIAL_CAPITAL)**(1/years)-1
    cal=ann/(max_dd) if max_dd>0 else 0

    # signals/day = total_t / days
    sigs_per_day = total_t / days if days > 0 else 0

    print(f"  Capital: {INITIAL_CAPITAL:,.0f} → {cash:,.0f} ₽")
    print(f"  Return:  {tr:+.1f}%  ({ann*100:+.1f}%/год)")
    print(f"  Max DD:  {max_dd*100:.1f}%")
    print(f"  Calmar:  {cal:.2f}")
    print(f"  Trades:  {total_t}  ({sigs_per_day:.1f}/day)")
    print(f"  WR:      {wins/total_t*100:.1f}% ({wins}/{total_t})")

    return {'tf': tf_rule, 'capital': cash, 'return_pct': tr, 'annual_return': ann*100,
            'max_dd_pct': max_dd*100, 'calmar': cal, 'wr': wins/total_t*100 if total_t else 0,
            'n_trades': total_t, 'trades_per_day': round(sigs_per_day, 1),
            'days': days}

if __name__ == '__main__':
    all_symbols = set()
    for lst in PORTFOLIO.values(): all_symbols.update(c[0] for c in lst)
    all_symbols = sorted(all_symbols)

    print("=" * 70)
    print("TF Sweep — Phase 5 Portfolio")
    print(f"Tickers: {all_symbols}")
    print(f"Period: 2025-01-01 to {TEST_END}")
    print("Kelly: 40-150%")
    print("=" * 70)

    import pickle
    print("\nLoading data from pickle cache...")
    with open('.tf_sweep_data.pkl', 'rb') as f:
        data_5m = pickle.load(f)
    print(f"Loaded {len(data_5m)} tickers")

    # Тестируем каждый TF
    tfs = [('5m', '5min'), ('15m', '15min'), ('H1', '1h')]
    results = []
    test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
    test_end_dt = datetime.strptime(TEST_END, '%Y-%m-%d') + timedelta(days=1)

    for tf_name, tf_rule in tfs:
        print(f"\n{'#' * 60}")
        print(f"# TF: {tf_name} (rule={tf_rule})")
        print(f"{'#' * 60}")
        t0 = time.time()

        print("Precomputing signals...")
        signals_all = precompute_signals_tf(data_5m, list(all_symbols), tf_rule)

        print("Simulating...")
        result = simulate_period(
            data_5m, signals_all, test_start, test_end_dt,
            kelly_min=0.40, kelly_max=1.50,
            label=f"TEST {tf_name}", tf_rule=tf_rule,
        )
        result['tf'] = tf_name
        result['time_s'] = round(time.time() - t0, 1)
        results.append(result)

    # Итоговая таблица
    print(f"\n{'=' * 70}")
    print(f"{'TF':6} {'Return':>10} {'Ann%':>8} {'DD':>8} {'Calmar':>8} {'Trades':>8} {'/day':>6} {'WR':>6} {'Time':>6}")
    print(f"{'─'*70}")
    for r in results:
        print(f"{r['tf']:6} {r['return_pct']:>+9.1f}% {r['annual_return']:>7.1f}% "
              f"{r['max_dd_pct']:>7.1f}% {r['calmar']:>8.2f} {r['n_trades']:>8} "
              f"{r['trades_per_day']:>6.1f} {r['wr']:>5.1f}% {r['time_s']:>5.1f}s")

    # Save
    os.makedirs('reports/tf_sweep', exist_ok=True)
    with open('reports/tf_sweep/results.json', 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: reports/tf_sweep/results.json")
