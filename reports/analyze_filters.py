#!/usr/bin/env python3
import psycopg2, pandas as pd, numpy as np, os

DB = {"host":"10.0.0.60","port":5432,"dbname":"moex",
      "user":os.environ.get("PGUSER","postgres"),
      "password":os.environ.get("PGPASSWORD","postgres")}

def q(sql):
    conn=psycopg2.connect(**DB); cur=conn.cursor(); cur.execute(sql)
    rows=cur.fetchall(); cols=[d[0] for d in cur.description]
    cur.close(); conn.close(); return cols, rows

def rz(df, tf):
    df=df.copy(); df.set_index("time",inplace=True)
    r=pd.DataFrame()
    r["open"]=df["open"].resample(tf).first()
    r["high"]=df["high"].resample(tf).max()
    r["low"]=df["low"].resample(tf).min()
    r["close"]=df["close"].resample(tf).last()
    r["volume"]=df["volume"].resample(tf).sum()
    return r.dropna()

def zs(s,w=20):
    m=s.rolling(w,min_periods=w).mean()
    std=s.rolling(w,min_periods=w).std()
    return ((s-m)/std).shift(1)

print("Loading 5m bars...")
c,r=q("SELECT time,open,high,low,close,volume FROM moex_prices_5m WHERE symbol='Si' ORDER BY time")
b5=pd.DataFrame(r,columns=c)
print(f"  {len(b5)} bars, {b5['time'].min()} -> {b5['time'].max()}")

print("Loading OI per clgroup...")
c,r=q("SELECT time,clgroup,buy_orders,sell_orders FROM openinterest_moex WHERE symbol='Si' ORDER BY time")
oi=pd.DataFrame(r,columns=c)
print(f"  {len(oi)} records")

print("Resampling...")
h4=rz(b5,"4h")
d1=rz(b5,"1d")
print(f"  H4: {len(h4)}, D1: {len(d1)}")

print("Building D1 OI...")
fiz=oi[oi["clgroup"]==1][["time","buy_orders","sell_orders"]].copy()
fiz.columns=["time","fiz_buy","fiz_sell"]
yur=oi[oi["clgroup"]==0][["time","buy_orders","sell_orders"]].copy()
yur.columns=["time","yur_buy","yur_sell"]
fiz.set_index("time",inplace=True); yur.set_index("time",inplace=True)
fiz_d1=fiz.resample("1D").last().dropna()
yur_d1=yur.resample("1D").last().dropna()
d1=d1.join(fiz_d1,how="inner").join(yur_d1,how="inner")
d1["fiz_net"]=d1["fiz_buy"]-d1["fiz_sell"]
d1["fiz_bias"]=(d1["fiz_buy"]-d1["fiz_sell"])/(d1["fiz_buy"]+d1["fiz_sell"]+1)
d1["fiz_bias_z"]=zs(d1["fiz_bias"],20)
print(f"  D1: {len(d1)} rows")
print(f"  fiz_bias range: [{d1['fiz_bias'].min():.4f}, {d1['fiz_bias'].max():.4f}]")

print("Volume Climax on H4...")
h4["vol_med"]=h4["volume"].rolling(20,min_periods=20).median().shift(1)
h4["vc"]=h4["volume"]>2*h4["vol_med"]
h4["vc_long"]=h4["vc"]&(h4["close"]==h4["high"])
h4["vc_short"]=h4["vc"]&(h4["close"]==h4["low"])
vcl=h4[h4["vc_long"]].copy(); vcs=h4[h4["vc_short"]].copy()
vca=pd.concat([vcl,vcs]).sort_index()
print(f"  VC signals: {len(vca)} (L:{len(vcl)}, S:{len(vcs)})")

def fwd(h4_,idx,n=1):
    loc=h4_.index.get_loc(idx)
    if loc+n<len(h4_):
        en=h4_.iloc[loc]["close"]; ex=h4_.iloc[loc+n]["close"]
        return (ex-en)/en
    return np.nan

results=[]
for idx in vca.index:
    il=idx in vcl.index
    r1=fwd(h4,idx,1)
    if not np.isnan(r1):
        results.append({"t":idx,"d":"L" if il else "S","r1":r1,"w":(r1>0) if il else (r1<0)})

bwr=sum(r["w"] for r in results)/len(results)
brt=np.mean([r["r1"] for r in results])
print(f"\nBASE VC WR: {bwr*100:.1f}% ({sum(r['w'] for r in results)}/{len(results)})")
print(f"AVG RET: {brt*100:.2f}%")

def gz(d1_,t_):
    d=pd.Timestamp(t_.date())
    return d1_.loc[d,"fiz_bias_z"] if d in d1_.index else np.nan

print("\n" + "="*70)
print("TEST 1: VC + Crowd Bias filter")
print("="*70)
flt=[]; rjt=[]
for r in results:
    z=gz(d1,r["t"])
    if np.isnan(z): continue
    cd="L" if z>0 else "S"
    if r["d"]==cd: flt.append(r)
    else: rjt.append(r)
fwr=sum(r["w"] for r in flt)/len(flt) if flt else 0
frt=np.mean([r["r1"] for r in flt]) if flt else 0
print(f"  Confirmed: {len(flt)}/{len(results)}, Rejected: {len(rjt)}")
print(f"  WR: {fwr*100:.1f}% ({sum(r['w'] for r in flt)}/{len(flt)})")
print(f"  AVG: {frt*100:.2f}%")

print("\n" + "="*70)
print("TEST 2: Whale Detector (D1 FIZ z extreme)")
print("="*70)
for t in [1.5,1.64,2.0,2.5,3.0]:
    wl=(d1["fiz_bias_z"]>t); ws=(d1["fiz_bias_z"]<-t)
    ns=wl.sum()+ws.sum()
    wls=[]
    for idx in d1[wl|ws].index:
        loc=d1.index.get_loc(idx)
        if loc+1<len(d1):
            il=wl.loc[idx]; en=d1.iloc[loc]["close"]; ex=d1.iloc[loc+1]["close"]
            rt=(ex-en)/en; wls.append((rt>0) if il else (rt<0))
    if wls: print(f"  |z|>{t:.2f}: {ns} sig, WR={sum(wls)/len(wls)*100:.1f}% ({sum(wls)}/{len(wls)})")

print("\n" + "="*70)
print("TEST 2b: Whale periods vs VC frequency")
print("="*70)
h4["d1d"]=[pd.Timestamp(idx.date()) for idx in h4.index]
for tl,tc in [("|z|>1.64",1.64),("|z|>2.0",2.0)]:
    wd=set(d1[abs(d1["fiz_bias_z"])>tc].index.date)
    vcon=h4[(h4["vc_long"]|h4["vc_short"])&h4["d1d"].isin(wd)]
    vcoff=h4[(h4["vc_long"]|h4["vc_short"])&~h4["d1d"].isin(wd)]
    wdd=len([d for d in set(h4["d1d"].values) if d in wd])
    nwd=len([d for d in set(h4["d1d"].values) if d not in wd])
    print(f"  {tl}: whale_days={wdd}, VC_on={len(vcon)} ({len(vcon)/max(1,wdd):.3f}/d), VC_off={len(vcoff)} ({len(vcoff)/max(1,nwd):.3f}/d)")

print("\n" + "="*70)
print("TEST 3: VC only with extreme |z|")
print("="*70)
for zt in [0.5,1.0,1.5,2.0]:
    fl=[]
    for r in results:
        z=gz(d1,r["t"])
        if not np.isnan(z) and abs(z)>=zt: fl.append(r)
    if fl:
        wr=sum(r["w"] for r in fl)/len(fl); rt=np.mean([r["r1"] for r in fl])
        print(f"  |z|>={zt:.1f}: {len(fl)}/{len(results)}, WR={wr*100:.1f}%, AVG={rt*100:.2f}%")

print("\n" + "="*70)
print("TEST 4: Anti-crowd")
print("="*70)
anti=[]
for r in results:
    z=gz(d1,r["t"])
    if np.isnan(z): continue
    ad="S" if z>0 else "L"
    if r["d"]==ad: anti.append(r)
awr=sum(r["w"] for r in anti)/len(anti) if anti else 0
art=np.mean([r["r1"] for r in anti]) if anti else 0
print(f"  Anti: {len(anti)}/{len(results)}, WR={awr*100:.1f}% ({sum(r['w'] for r in anti)}/{len(anti)}), AVG={art*100:.2f}%")

for zt in [0.5,1.0,1.5,2.0]:
    fl=[]
    for r in results:
        z=gz(d1,r["t"])
        if np.isnan(z) or abs(z)<zt: continue
        ad="S" if z>0 else "L"
        if r["d"]==ad: fl.append(r)
    if fl:
        wr=sum(r["w"] for r in fl)/len(fl); rt=np.mean([r["r1"] for r in fl])
        print(f"  Anti |z|>={zt:.1f}: {len(fl)} sig, WR={wr*100:.1f}%, AVG={rt*100:.2f}%")

print("\n" + "="*70)
print("MATCH RATE")
print("="*70)
mt=0; tt=0
for r in results:
    z=gz(d1,r["t"])
    if np.isnan(z): continue
    tt+=1; cd="L" if z>0 else "S"
    if r["d"]==cd: mt+=1
print(f"  Same: {mt}/{tt} = {mt/tt*100:.1f}%")
print(f"  Opposite: {tt-mt}/{tt} = {(tt-mt)/tt*100:.1f}%")

print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print(f"{'Test':<50} {'Sig':<8} {'WR':<8} {'AvgRet':<8}")
print("-"*74)
print(f"{'1. Base VolClimax (H4)':<50} {len(results):<8} {bwr*100:.1f}%{'':<5} {brt*100:.2f}%")
print(f"{'   + Crowd Bias filter':<50} {len(flt):<8} {fwr*100:.1f}%{'':<5} {frt*100:.2f}%")
print(f"{'4. Anti-crowd':<50} {len(anti):<8} {awr*100:.1f}%{'':<5} {art*100:.2f}%")
print("\nDONE!")
