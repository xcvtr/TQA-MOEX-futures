#!/usr/bin/env python3
import psycopg2, pandas as pd, numpy as np, os

DB = {"host":"10.0.0.60","port":5432,"dbname":"moex",
      "user":os.environ.get("PGUSER","postgres"),
      "password":os.environ.get("PGPASSWORD","postgres")}

def q(sql):
    conn=psycopg2.connect(**DB); cur=conn.cursor(); cur.execute(sql)
    rows=cur.fetchall(); cols=[d[0] for d in cur.description]
    cur.close(); conn.close(); return cols, rows

def zs(s,w=20):
    m=s.rolling(w,min_periods=w).mean()
    std=s.rolling(w,min_periods=w).std()
    return ((s-m)/std).shift(1)

print("="*70)
print("ANALIZ FILTROV: Volume Climax & Whale Detector")
print("Si (USDRUB futures)")
print("="*70)

# === LOAD 5m PRICES ===
print("\n[1] Loading 5m prices...")
c,r=q("SELECT time,open,high,low,close,volume FROM moex_prices_5m WHERE symbol='Si' ORDER BY time")
b5=pd.DataFrame(r,columns=c)
print(f"  {len(b5)} bars")

# === LOAD 5m OI AGGREGATES (fiz_buy, fiz_sell, yur_buy, yur_sell) ===
print("\n[2] Loading 5m OI aggregates...")
c,r=q("SELECT time,fiz_buy,fiz_sell,yur_buy,yur_sell,total_oi FROM moex_prices_5m_oi WHERE symbol='Si' ORDER BY time")
oi5=pd.DataFrame(r,columns=c)
print(f"  {len(oi5)} records")

# === RESAMPLE TO H4 ===
print("\n[3] Resampling to H4...")
b5i=b5.copy(); b5i.set_index("time",inplace=True)
h4=pd.DataFrame()
for col in ["open","high","low","close","volume"]:
    h4[col]=b5i[col].resample("4h").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}[col])
h4.dropna(inplace=True)
print(f"  H4: {len(h4)} bars")

# === H4 Volume Climax ===
print("\n[4] Volume Climax detection...")
h4["vol_med"]=h4["volume"].rolling(20,min_periods=20).median().shift(1)
h4["vol_mean"]=h4["volume"].rolling(20,min_periods=20).mean().shift(1)
h4["vol_std"]=h4["volume"].rolling(20,min_periods=20).std().shift(1)

# VC signals: vol > 2*median (strict) or vol > mean+2*std (alternative)
h4["vc_strict"]=h4["volume"]>2*h4["vol_med"]
h4["vc_std"]=h4["volume"]>(h4["vol_mean"]+2*h4["vol_std"])
h4["vc_any"]=h4["vc_strict"]|h4["vc_std"]

# Close near extreme (within 0.2%)
h4["body_pct"]=(h4["close"]-h4["open"])/(h4["high"]-h4["low"]+0.001)
h4["near_high"]=(h4["high"]-h4["close"])/(h4["high"]-h4["low"]+0.001)<0.2
h4["near_low"]=(h4["close"]-h4["low"])/(h4["high"]-h4["low"]+0.001)<0.2
h4["near_extreme"]=h4["near_high"]|h4["near_low"]

# Combined: VC + near extreme
h4["vc_sig_long"]=h4["vc_any"]&h4["near_high"]
h4["vc_sig_short"]=h4["vc_any"]&h4["near_low"]
h4["vc_sig"]=h4["vc_sig_long"]|h4["vc_sig_short"]

vc_n_strict=h4["vc_strict"].sum()
vc_n_std=h4["vc_std"].sum()
vc_n_any=h4["vc_any"].sum()
vc_n_sig=(h4["vc_sig_long"]|h4["vc_sig_short"]).sum()
print(f"  VC (vol>2*median): {vc_n_strict}")
print(f"  VC (vol>mean+2*std): {vc_n_std}")
print(f"  VC (any anomaly): {vc_n_any}")
print(f"  VC + near extreme: {vc_n_sig}")
print(f"    Longs: {h4['vc_sig_long'].sum()}, Shorts: {h4['vc_sig_short'].sum()}")

# === RESAMPLE TO D1 ===
print("\n[5] Resampling to D1...")
d1=pd.DataFrame()
for col in ["open","high","low","close","volume"]:
    d1[col]=b5i[col].resample("1d").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}[col])
d1.dropna(inplace=True)

# OI to D1
oi5i=oi5.copy(); oi5i.set_index("time",inplace=True)
oi_d1=pd.DataFrame()
for col in ["fiz_buy","fiz_sell","yur_buy","yur_sell","total_oi"]:
    oi_d1[col]=oi5i[col].resample("1d").last().dropna()
oi_d1.dropna(inplace=True)

# Merge
d1=d1.join(oi_d1,how="inner")
d1["fiz_net"]=d1["fiz_buy"]-d1["fiz_sell"]
d1["yur_net"]=d1["yur_buy"]-d1["yur_sell"]
d1["fiz_bias"]=(d1["fiz_buy"]-d1["fiz_sell"])/(d1["fiz_buy"]+d1["fiz_sell"]+1)
d1["yur_bias"]=(d1["yur_buy"]-d1["yur_sell"])/(d1["yur_buy"]+d1["yur_sell"]+1)
d1["fiz_ratio"]=d1["fiz_buy"]/(d1["fiz_buy"]+d1["fiz_sell"]+1)
d1["yur_ratio"]=d1["yur_buy"]/(d1["yur_buy"]+d1["yur_sell"]+1)

# Relative crowding: who's more extreme?
d1["fiz_vs_yur"]=d1["fiz_bias"]-d1["yur_bias"]

# Z-scores (no look-ahead)
for col in ["fiz_bias","yur_bias","fiz_ratio","yur_ratio","fiz_vs_yur"]:
    d1[col+"_z"]=zs(d1[col],20)

print(f"  D1: {len(d1)} days")
print(f"  fiz_bias range: [{d1['fiz_bias'].min():.4f}, {d1['fiz_bias'].max():.4f}]")
print(f"  yur_bias range: [{d1['yur_bias'].min():.4f}, {d1['yur_bias'].max():.4f}]")

# === FORWARD TEST FOR VC SIGNALS ===
print("\n[6] Forward testing VC signals...")
def fwd(h4_,idx,n=1):
    loc=h4_.index.get_loc(idx)
    if loc+n<len(h4_):
        return (h4_.iloc[loc+n]["close"]-h4_.iloc[loc]["close"])/h4_.iloc[loc]["close"]
    return np.nan

# Get D1 Z for a given H4 time
def get_d1z(d1_,t_):
    d=pd.Timestamp(t_.date())
    if d in d1_.index:
        return (d1_.loc[d,"fiz_bias_z"], d1_.loc[d,"yur_bias_z"], 
                d1_.loc[d,"fiz_vs_yur_z"], d1_.loc[d,"fiz_bias"])
    return (np.nan,np.nan,np.nan,np.nan)

# Collect all VC signals
vcs=[]
for idx in h4[h4["vc_sig"]].index:
    il=idx in h4[h4["vc_sig_long"]].index
    r1=fwd(h4,idx,1); r2=fwd(h4,idx,2); r4=fwd(h4,idx,4)
    if not np.isnan(r1):
        fiz_z,yur_z,fvy_z,fiz_b=get_d1z(d1,idx)
        vcs.append({"t":idx,"d":"L" if il else "S","r1":r1,"r2":r2,"r4":r4,
                     "w":(r1>0) if il else (r1<0),
                     "fiz_z":fiz_z,"yur_z":yur_z,"fvy_z":fvy_z,"fiz_b":fiz_b})
print(f"  {len(vcs)} VC signals with forward data")

if vcs:
    bwr=sum(v["w"] for v in vcs)/len(vcs)
    brt=np.mean([v["r1"] for v in vcs])
    print(f"\n  BASE VC WR (1H4): {bwr*100:.1f}% ({sum(v['w'] for v in vcs)}/{len(vcs)})")
    print(f"  AVG RET: {brt*100:.2f}%")
    
    # TEST 1: VC + Crowd Bias filter (confirm direction)
    flt=[]; rjt=[]
    for v in vcs:
        if np.isnan(v["fiz_z"]): continue
        cd="L" if v["fiz_z"]>0 else "S"
        if v["d"]==cd: flt.append(v)
        else: rjt.append(v)
    
    print(f"\n{'='*70}")
    print("TEST 1: VC + FIZ Crowd Bias filter")
    print(f"{'='*70}")
    fwr=sum(v["w"] for v in flt)/len(flt) if flt else 0
    frt=np.mean([v["r1"] for v in flt]) if flt else 0
    print(f"  Confirmed: {len(flt)}/{len(vcs)}, Rejected: {len(rjt)}")
    print(f"  WR: {fwr*100:.1f}% ({sum(v['w'] for v in flt)}/{len(flt)})")
    print(f"  AVG: {frt*100:.2f}%")
    
    # TEST 1b: VC + YUR Crowd Bias filter
    flt2=[]
    for v in vcs:
        if np.isnan(v["yur_z"]): continue
        cd="L" if v["yur_z"]>0 else "S"
        if v["d"]==cd: flt2.append(v)
    fwr2=sum(v["w"] for v in flt2)/len(flt2) if flt2 else 0
    print(f"\n  VC + YUR bias: {len(flt2)}/{len(vcs)}, WR={fwr2*100:.1f}%")
    
    # TEST 1c: VC + fiz_vs_yur (divergence between fiz and yur)
    flt3=[]
    for v in vcs:
        if np.isnan(v["fvy_z"]): continue
        cd="L" if v["fvy_z"]>0 else "S"
        if v["d"]==cd: flt3.append(v)
    fwr3=sum(v["w"] for v in flt3)/len(flt3) if flt3 else 0
    print(f"  VC + FIZvsYUR: {len(flt3)}/{len(vcs)}, WR={fwr3*100:.1f}%")
    
    # TEST 3: VC only when extreme |fiz_z|
    print(f"\n{'='*70}")
    print("TEST 3: VC only with extreme FIZ |z|")
    print(f"{'='*70}")
    for zt in [0.5,1.0,1.5,2.0]:
        fl=[]
        for v in vcs:
            if not np.isnan(v["fiz_z"]) and abs(v["fiz_z"])>=zt: fl.append(v)
        if fl:
            wr=sum(v["w"] for v in fl)/len(fl); rt=np.mean([v["r1"] for v in fl])
            print(f"  |fiz_z|>={zt:.1f}: {len(fl)}/{len(vcs)}, WR={wr*100:.1f}%, AVG={rt*100:.2f}%")
    
    # TEST 4: Anti-crowd
    print(f"\n{'='*70}")
    print("TEST 4: Anti-crowd (trade AGAINST FIZ)")
    print(f"{'='*70}")
    anti=[]
    for v in vcs:
        if np.isnan(v["fiz_z"]): continue
        ad="S" if v["fiz_z"]>0 else "L"
        if v["d"]==ad: anti.append(v)
    awr=sum(v["w"] for v in anti)/len(anti) if anti else 0
    art=np.mean([v["r1"] for v in anti]) if anti else 0
    print(f"  Anti: {len(anti)}/{len(vcs)}, WR={awr*100:.1f}%, AVG={art*100:.2f}%")
    
    # Anti-crowd with thresholds
    for zt in [0.5,1.0,1.5,2.0]:
        fl=[]
        for v in vcs:
            if np.isnan(v["fiz_z"]) or abs(v["fiz_z"])<zt: continue
            ad="S" if v["fiz_z"]>0 else "L"
            if v["d"]==ad: fl.append(v)
        if fl:
            wr=sum(v["w"] for v in fl)/len(fl); rt=np.mean([v["r1"] for v in fl])
            print(f"  Anti |z|>={zt:.1f}: {len(fl)} sig, WR={wr*100:.1f}%, AVG={rt*100:.2f}%")

# === WHALE DETECTOR ===
print(f"\n{'='*70}")
print("TEST 2: Whale Detector (D1 OI extremes)")
print(f"{'='*70}")

# Whale = extreme FIZ bias (crowd) OR extreme YUR bias (smart money)
for col,label in [("fiz_bias","FIZ(crowd)"),("yur_bias","YUR(smart)"),("fiz_vs_yur","FIZvsYUR")]:
    print(f"\n  --- {label} ---")
    for t in [1.5,1.64,2.0,2.5,3.0]:
        wl=(d1[col+"_z"]>t); ws=(d1[col+"_z"]<-t)
        ns=wl.sum()+ws.sum()
        wls=[]; rts=[]
        for idx in d1[wl|ws].index:
            loc=d1.index.get_loc(idx)
            if loc+1<len(d1):
                il=wl.loc[idx]
                ret=(d1.iloc[loc+1]["close"]-d1.iloc[loc]["close"])/d1.iloc[loc]["close"]
                wls.append((ret>0) if il else (ret<0))
                rts.append(ret)
        if wls:
            wr_pct=sum(wls)/len(wls)*100
            print(f"    |z|>{t:.2f}: {ns} sig, WR={wr_pct:.1f}% ({sum(wls)}/{len(wls)}), AVG={np.mean(rts)*100:.2f}%")

# === WHALE vs VC frequency ===
print(f"\n{'='*70}")
print("TEST 2b: Whale periods vs VC frequency")
print(f"{'='*70}")

# Days where FIZ bias is extreme
wd_1_64=set(d1[abs(d1["fiz_bias_z"])>1.64].index.date)
wd_2_0=set(d1[abs(d1["fiz_bias_z"])>2.0].index.date)
wd_2_5=set(d1[abs(d1["fiz_bias_z"])>2.5].index.date)

h4["d1d"]=[pd.Timestamp(idx.date()) for idx in h4.index]

for label,wd in [("|z|>1.64",wd_1_64),("|z|>2.0",wd_2_0),("|z|>2.5",wd_2_5)]:
    vcon=h4[h4["vc_sig"]&h4["d1d"].isin(wd)]
    vcoff=h4[h4["vc_sig"]&~h4["d1d"].isin(wd)]
    wdd=len([d for d in set(h4["d1d"].values) if d in wd])
    nwd=len([d for d in set(h4["d1d"].values) if d not in wd])
    all_vc_wd=h4[h4["vc_any"]&h4["d1d"].isin(wd)]
    all_vc_nowd=h4[h4["vc_any"]&~h4["d1d"].isin(wd)]
    print(f"\n  {label}:")
    print(f"    Whale days: {wdd}, Non-whale: {nwd}")
    print(f"    VC+sig on whale days: {len(vcon)} ({len(vcon)/max(1,wdd):.4f}/d)")
    print(f"    VC+sig on non-whale: {len(vcoff)} ({len(vcoff)/max(1,nwd):.4f}/d)")
    print(f"    VC+any on whale: {len(all_vc_wd)} ({len(all_vc_wd)/max(1,wdd):.4f}/d)")
    print(f"    VC+any on non-whale: {len(all_vc_nowd)} ({len(all_vc_nowd)/max(1,nwd):.4f}/d)")

# === MATCH RATE ===
print(f"\n{'='*70}")
print("MATCH RATE: VC direction vs FIZ bias")
print(f"{'='*70}")
mt=0; tt=0
for v in vcs:
    if np.isnan(v["fiz_z"]): continue
    tt+=1; cd="L" if v["fiz_z"]>0 else "S"
    if v["d"]==cd: mt+=1
print(f"  Same direction: {mt}/{tt} = {mt/tt*100:.1f}%")
print(f"  Opposite: {tt-mt}/{tt} = {(tt-mt)/tt*100:.1f}%")

# === ADDITIONAL: What about combining FIZ and YUR? ===
print(f"\n{'='*70}")
print("BONUS: Combined FIZ+YUR divergence signals")
print(f"{'='*70}")

# When FIZ and YUR disagree (fiz_vs_yur extreme), who wins?
for t in [1.5,2.0,2.5]:
    div_sig=abs(d1["fiz_vs_yur_z"])>t
    ns=div_sig.sum()
    wls=[]
    for idx in d1[div_sig].index:
        loc=d1.index.get_loc(idx)
        if loc+1<len(d1):
            fz=d1.iloc[loc]["fiz_bias_z"]
            yz=d1.iloc[loc]["yur_bias_z"]
            # If fiz > yur (crowd buying more than smart) → smart might be right
            # If yur > fiz (smart buying more) → smart likely right
            if abs(fz)>abs(yz):
                # Crowd driving the divergence → fade crowd
                fade_dir="S" if fz>0 else "L"
            else:
                # Smart driving → follow
                fade_dir="L" if yz>0 else "S"
            ret=(d1.iloc[loc+1]["close"]-d1.iloc[loc]["close"])/d1.iloc[loc]["close"]
            wls.append((ret>0) if fade_dir=="L" else (ret<0))
    if wls:
        print(f"  Divergence |z|>{t:.1f}: {ns} sig, WR={sum(wls)/len(wls)*100:.1f}% ({sum(wls)}/{len(wls)})")

print("\nDONE!")
