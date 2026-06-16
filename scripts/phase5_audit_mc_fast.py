#!/usr/bin/env python3
"""Monte Carlo shuffle for Phase 5 strategy - GL ticker on 1h bars"""
import clickhouse_connect, numpy as np, pandas as pd, json, os

def rz(s,w=20):
    m=s.rolling(w,min_periods=w).mean();std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

ch=clickhouse_connect.get_client(host='127.0.0.1',port=8123)

print("Loading GL...")
q="""SELECT p.time,p.close,p.high,p.low,p.volume,
            o.fiz_buy,o.fiz_sell,o.yur_buy,o.yur_sell
     FROM moex.prices_5m p
     LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
     WHERE p.symbol='GL' AND p.time>='2024-01-01' AND p.time<='2026-04-30'
     ORDER BY p.time"""
r=ch.query(q)
cols=['time','close','high','low','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
df=pd.DataFrame(r.result_rows,columns=cols)
df['time']=pd.to_datetime(df['time']);df.set_index('time',inplace=True)

d=df.resample('1h').agg({'close':'last','high':'max','low':'min','volume':'sum',
    'fiz_buy':'sum','fiz_sell':'sum','yur_buy':'sum','yur_sell':'sum'}).dropna()

d['volume']=d['volume'].astype(float)
d['vma20']=d['volume'].rolling(20).mean().fillna(d['volume'])
d['vr']=d['volume']/d['vma20'].clip(lower=1);d['vz']=rz(d['volume'],20)
d['fiz_net']=d['fiz_buy'].fillna(0)-d['fiz_sell'].fillna(0)
d['yur_net']=d['yur_buy'].fillna(0)-d['yur_sell'].fillna(0)
d['oi_r']=(d['yur_buy']+d['yur_sell']).fillna(0)/(d['fiz_buy']+d['fiz_sell']+1).fillna(0)
d['oima']=d['oi_r'].rolling(20).mean();d['atr14']=calc_atr(d)
d['atr_pct']=d['atr14']/d['close'].clip(lower=1)*100

vs=np.clip((d['vr']-1.5)/3.0,0,1)
os_=np.clip((d['oima']-d['oi_r'])/d['oima'].clip(lower=0.1),0,1)
raw=vs*0.6+os_*0.4
af=np.clip(1-(d['atr_pct']-0.3)/3.0,0,1)
d['score']=np.clip(raw*af*np.clip(1+d['vz']/5,0.5,1.5),0,1)

test=d.loc['2025-01-01':'2026-04-30'].copy()
test['next_close']=test['close'].shift(-1)
print(f"Test period: {len(test)} 1h bars")

mask_real=(test['score']>=0.25)&(~np.isnan(test['score']))&(test['atr14']>0)
real_pnl=np.nansum((test.loc[mask_real,'next_close']-test.loc[mask_real,'close'])/test.loc[mask_real,'close']*5000*1)
print(f"Real PnL: {real_pnl:+,.0f} | trades: {mask_real.sum()}")

mc_pnls=[]
for mc in range(50):
    shuffled=test['score'].values.copy()
    np.random.shuffle(shuffled)
    test_shuff=test.copy()
    test_shuff['shuffled_score']=shuffled
    m=(test_shuff['shuffled_score']>=0.25)&(~np.isnan(test_shuff['shuffled_score']))&(test_shuff['atr14']>0)
    pnl=np.nansum((test_shuff.loc[m,'next_close']-test_shuff.loc[m,'close'])/test_shuff.loc[m,'close']*5000*1)
    mc_pnls.append(float(pnl))
    if (mc+1)%10==0:
        print(f"  [{mc+1}/50] current={mc_pnls[-1]:+.0f}")

mc_arr=np.array(mc_pnls)
p95=np.percentile(mc_arr,95)
p99=np.percentile(mc_arr,99)

result={
    'real_pnl':float(real_pnl),
    'mc_mean':float(mc_arr.mean()),
    'mc_std':float(mc_arr.std()),
    'mc_p95':float(p95),
    'mc_p99':float(p99),
    'mc_min':float(mc_arr.min()),
    'mc_max':float(mc_arr.max()),
    'real_gt_p95':bool(real_pnl>p95),
    'mc_lt_real':int(np.sum(mc_arr<real_pnl)),
}

print(f"\n{'='*50}")
print(f"MONTE CARLO SHUFFLE (GL H1, 50x)")
print(f"{'='*50}")
print(f"Реальный PnL:     {real_pnl:+,.0f}")
print(f"MC среднее:       {mc_arr.mean():+,.0f}")
print(f"MC std:           {mc_arr.std():+,.0f}")
print(f"MC P95:           {p95:+,.0f}")
print(f"MC P99:           {p99:+,.0f}")
print(f"MC min/max:       {mc_arr.min():+,.0f} / {mc_arr.max():+,.0f}")
print(f"Real > MC P95:    {'YES' if real_pnl>p95 else 'NO'}")
print(f"MC < real:        {np.sum(mc_arr<real_pnl)}/{len(mc_arr)} ({np.sum(mc_arr<real_pnl)/len(mc_arr)*100:.0f}%)")

os.makedirs('reports/phase5_walkforward',exist_ok=True)
with open('reports/phase5_walkforward/mc_audit.json','w') as f:
    json.dump(result,f,indent=2)
print(f"\nSaved: reports/phase5_walkforward/mc_audit.json")
