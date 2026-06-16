#!/usr/bin/env python3
"""YD quick: Yur Divergence tests."""
import sys, os
from datetime import datetime, timedelta, timezone
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd, psycopg2
from scripts.bar_level_sim import BarLevelPortfolio, TICKER_CONFIGS

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='')
ALL_TICKERS = sorted(TICKER_CONFIGS.keys())
DAYS = 365

def _zs(vals, w=20):
    out = [0.0]*len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk)/w
        sd = (sum((x-mu)**2 for x in chunk)/w)**0.5
        out[i] = (vals[i]-mu)/sd if sd>0 else 0
    return out

print("Loading whale tickers...")
since = datetime.now(timezone.utc)-timedelta(days=DAYS)
conn = psycopg2.connect(**DB)
cur = conn.cursor()
cur.execute('''SELECT symbol, AVG((yur_buy::float+yur_sell::float)/NULLIF(total_oi::float,0)) as ys FROM moex_prices_5m_oi WHERE time>=%s AND total_oi>0 GROUP BY symbol''', (since,))
whale = {r[0] for r in cur.fetchall() if r[1] > 0.50 and r[0] in ALL_TICKERS}
print(f"Whale tickers: {len(whale)}")

tf = {}
for sym in sorted(whale):
    cur = conn.cursor()
    cur.execute("SELECT time,open,high,low,close,volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
    ohlcv = [{'time':r[0],'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4]),'volume':float(r[5])} for r in cur]
    cur.close()
    if len(ohlcv)<100: continue
    cur = conn.cursor()
    cur.execute("SELECT time,fiz_buy,fiz_sell,yur_buy,yur_sell,total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
    oi = [{'time':r[0],'fiz_buy':float(r[1]),'fiz_sell':float(r[2]),'yur_buy':float(r[3]),'yur_sell':float(r[4]),'total_oi':float(r[5])} for r in cur]
    cur.close()
    om = {r['time'].strftime('%Y-%m-%d %H:%M'):r for r in oi}
    merged = []
    for r in ohlcv:
        o=om.get(r['time'].strftime('%Y-%m-%d %H:%M'))
        if o is None: continue
        merged.append({**r,'total_oi':o['total_oi'],'fiz_buy':o['fiz_buy'],'fiz_sell':o['fiz_sell'],'yur_buy':o['yur_buy'],'yur_sell':o['yur_sell'],'symbol':sym})
    if len(merged)<100: continue
    # Resample
    df=pd.DataFrame(merged)
    df['time']=pd.to_datetime(df['time'])
    df.set_index('time',inplace=True)
    agg={'open':'first','high':'max','low':'min','close':'last','volume':'sum','total_oi':'last','fiz_buy':'last','fiz_sell':'last','yur_buy':'last','yur_sell':'last'}
    res=df.resample('1h').agg(agg).dropna(subset=['close'])
    out=[{'time':idx,'open':float(row['open']),'high':float(row['high']),'low':float(row['low']),'close':float(row['close']),'volume':float(row['volume']),'total_oi':float(row['total_oi']),'fiz_buy':float(row['fiz_buy']),'fiz_sell':float(row['fiz_sell']),'yur_buy':float(row['yur_buy']),'yur_sell':float(row['yur_sell']),'symbol':sym} for idx,row in res.iterrows()]
    if len(out)>=50: tf[sym]=out
conn.close()
print(f"H1 tickers: {len(tf)}")

# Test function
def test_yur(label, div_th, horizon, min_gap, mt):
    sigs=[]
    for sym,rows in tf.items():
        n=len(rows)
        yur_net=[(r['yur_buy']-r['yur_sell'])/max(r['yur_buy']+r['yur_sell'],1) for r in rows]
        yz=_zs(yur_net,20)
        chg=[0.0]*n
        for i in range(1,n):
            chg[i]=(rows[i]['close']-rows[i-1]['close'])/rows[i-1]['close']*100
        pz=_zs(chg,20)
        last={}
        for i in range(25,n-horizon):
            if sym in last and i-last[sym]<min_gap:
                continue
            if yz[i]>div_th and pz[i]<-div_th:
                direction='LONG'
            elif yz[i]<-div_th and pz[i]>div_th:
                direction='SHORT'
            else:
                continue
            entry=rows[i+1]['open']
            if entry<=0: continue
            exit_p=rows[i+horizon]['close']
            if direction=='LONG':
                ret=(exit_p-entry)/entry*100
            else:
                ret=(entry-exit_p)/entry*100
            sigs.append({'ticker':sym,'direction':direction,'entry':round(entry,4),'exit':round(exit_p,4),'time':str(rows[i]['time']),'return_pct':round(ret,4),'score':0.5,'atr_pct':0.01,'adx_value':20})
            last[sym]=i
    if len(sigs)<3:
        print(f"  {label}: {len(sigs)} sigs - too few")
        return
    for s in sigs:
        s['_time_dt']=pd.Timestamp(s['time'])
    
    p=BarLevelPortfolio(initial_capital=mt['cap'], max_dd=0.20, margin_usage=mt['margin'],
        max_concurrent=mt['mc'], total_margin_limit=0.20, stop_loss_pct=mt['sl'],
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=mt['trail'], trailing_mult=3.0,
        max_hold_bars=mt['hold'], allow_rollover=mt['roll'])
    r=p.run(sigs)
    wr=sum(1 for t in r['trades'] if t['pnl']>0)/len(r['trades'])*100 if r['trades'] else 0
    ret=r['total_return_pct']; dd=r['max_dd_pct']; cal=r['calmar']; tr=len(r['trades'])
    roll=sum(1 for t in r['trades'] if t['exit_reason']=='rollover')/tr*100 if tr else 0
    print(f"  {label}: sigs={len(sigs):>5} ret={ret:>7.2f}% DD={dd:>6.2f}% Calmar={cal:.4f} T={tr:>2} WR={wr:.0f}% Roll={roll:.0f}%")
    for t in r['trades']:
        print(f"    {t['ticker']:>6} {t['direction']:5} {t['exit_reason']:10} PnL={t['pnl']:>+8.0f}")
    return sigs, r

print("\n=== Yur Divergence Tests ===\n")

# Test matrix
tests = [
    ("YD-1.0 h=6 100K noroll", 1.0, 6, 0, dict(cap=100000, mc=8, margin=0.10, sl=0.02, hold=40, roll=False, trail=True)),
    ("YD-0.8 h=6 100K noroll", 0.8, 6, 0, dict(cap=100000, mc=8, margin=0.10, sl=0.02, hold=40, roll=False, trail=True)),
    ("YD-1.0 h=12 100K noroll", 1.0, 12, 0, dict(cap=100000, mc=8, margin=0.10, sl=0.02, hold=40, roll=False, trail=True)),
    ("YD-0.8 h=12 100K noroll", 0.8, 12, 0, dict(cap=100000, mc=8, margin=0.10, sl=0.02, hold=40, roll=False, trail=True)),
]

for label, dt, h, mg, mt in tests:
    test_yur(label, dt, h, mg, mt)
    print()

print("\n=== Higher Capital ===\n")
big_tests = [
    ("YD-1.0 h=6 200K noroll", 1.0, 6, 0, dict(cap=200000, mc=16, margin=0.10, sl=0.02, hold=40, roll=False, trail=True)),
    ("YD-0.8 h=6 200K noroll", 0.8, 6, 0, dict(cap=200000, mc=16, margin=0.10, sl=0.02, hold=40, roll=False, trail=True)),
    ("YD-1.0 h=6 300K noroll", 1.0, 6, 0, dict(cap=300000, mc=24, margin=0.10, sl=0.02, hold=40, roll=False, trail=True)),
]
for label, dt, h, mg, mt in big_tests:
    test_yur(label, dt, h, mg, mt)
    print()

# WF for the best one
print("\n=== Walk-Forward: YD-1.0 h=6 200K ===\n")
sigs=[]
for sym,rows in tf.items():
    n=len(rows)
    yur_net=[(r['yur_buy']-r['yur_sell'])/max(r['yur_buy']+r['yur_sell'],1) for r in rows]
    yz=_zs(yur_net,20)
    chg=[0.0]*n
    for i in range(1,n):
        chg[i]=(rows[i]['close']-rows[i-1]['close'])/rows[i-1]['close']*100
    pz=_zs(chg,20)
    for i in range(25,n-6):
        if yz[i]>1.0 and pz[i]<-1.0:
            direction='LONG'
        elif yz[i]<-1.0 and pz[i]>1.0:
            direction='SHORT'
        else: continue
        entry=rows[i+1]['open']
        if entry<=0: continue
        exit_p=rows[i+6]['close']
        ret=(exit_p-entry)/entry*100 if direction=='LONG' else (entry-exit_p)/entry*100
        sigs.append({'ticker':sym,'direction':direction,'entry':round(entry,4),'exit':round(exit_p,4),'time':str(rows[i]['time']),'return_pct':round(ret,4),'score':0.5,'atr_pct':0.01,'adx_value':20})
for s in sigs:
    s['_time_dt']=pd.Timestamp(s['time'])

params=dict(initial_capital=200000, max_dd=0.20, margin_usage=0.10,
    max_concurrent=16, total_margin_limit=0.20, stop_loss_pct=0.02,
    use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
    use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
    max_hold_bars=40, allow_rollover=False)

n4=len(sigs)//4
for fi in range(4):
    fs=sigs[fi*n4:(fi+1)*n4]
    if len(fs)<5: continue
    groups={}
    for s in fs:
        groups.setdefault(s['_time_dt'],[]).append(s)
    st=sorted(groups.keys())
    pw=BarLevelPortfolio(**params)
    rw=pw._run_grouped(st,groups)
    tr=len(rw['trades'])
    print(f"  Fold {fi+1}: ret={rw['total_return_pct']:.2f}% DD={rw['max_dd_pct']:.2f}% Calmar={rw['calmar']:.4f} T={tr}")