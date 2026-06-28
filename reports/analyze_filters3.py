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
print("ANALIZ FILTROV V2: Detalniy razbor")
print("Si (USDRUB)")
print("="*70)

# Load all data
c,r=q("SELECT time,open,high,low,close,volume FROM moex_prices_5m WHERE symbol='Si' ORDER BY time")
b5=pd.DataFrame(r,columns=c)
c,r=q("SELECT time,fiz_buy,fiz_sell,yur_buy,yur_sell,total_oi FROM moex_prices_5m_oi WHERE symbol='Si' ORDER BY time")
oi5=pd.DataFrame(r,columns=c)

# Resample to H4
b5i=b5.copy(); b5i.set_index("time",inplace=True)
h4=pd.DataFrame()
h4["open"]=b5i["open"].resample("4h").first()
h4["high"]=b5i["high"].resample("4h").max()
h4["low"]=b5i["low"].resample("4h").min()
h4["close"]=b5i["close"].resample("4h").last()
h4["volume"]=b5i["volume"].resample("4h").sum()
h4.dropna(inplace=True)

# Resample to D1
d1=pd.DataFrame()
d1["open"]=b5i["open"].resample("1D").first()
d1["high"]=b5i["high"].resample("1D").max()
d1["low"]=b5i["low"].resample("1D").min()
d1["close"]=b5i["close"].resample("1D").last()
d1["volume"]=b5i["volume"].resample("1D").sum()
d1.dropna(inplace=True)

oi5i=oi5.copy(); oi5i.set_index("time",inplace=True)
oid1=pd.DataFrame()
oid1["fiz_buy"]=oi5i["fiz_buy"].resample("1D").last()
oid1["fiz_sell"]=oi5i["fiz_sell"].resample("1D").last()
oid1["yur_buy"]=oi5i["yur_buy"].resample("1D").last()
oid1["yur_sell"]=oi5i["yur_sell"].resample("1D").last()
oid1["total_oi"]=oi5i["total_oi"].resample("1D").last()
oid1.dropna(inplace=True)

d1=d1.join(oid1,how="inner")
d1["fiz_net"]=d1["fiz_buy"]-d1["fiz_sell"]
d1["yur_net"]=d1["yur_buy"]-d1["yur_sell"]
d1["fiz_bias"]=(d1["fiz_buy"]-d1["fiz_sell"])/(d1["fiz_buy"]+d1["fiz_sell"]+1)
d1["yur_bias"]=(d1["yur_buy"]-d1["yur_sell"])/(d1["yur_buy"]+d1["yur_sell"]+1)

# Multiple z-score windows
for w in [10,20,30,50]:
    d1[f"fiz_z_{w}"]=zs(d1["fiz_bias"],w)
    d1[f"yur_z_{w}"]=zs(d1["yur_bias"],w)
    d1[f"fiz_yur_diff_{w}"]=zs(d1["fiz_bias"]-d1["yur_bias"],w)

# Volume Climax with multiple thresholds
h4["vol_med20"]=h4["volume"].rolling(20,min_periods=20).median().shift(1)
h4["vol_mean20"]=h4["volume"].rolling(20,min_periods=20).mean().shift(1)
h4["vol_std20"]=h4["volume"].rolling(20,min_periods=20).std().shift(1)

def vc_sigs(h4_,vol_col,thresh):
    """Generate VC signals with given volume threshold"""
    h4_["_vc"]=h4_[vol_col]>thresh
    h4_["_near_high"]=(h4_["high"]-h4_["close"])/(h4_["high"]-h4_["low"]+0.001)<0.15
    h4_["_near_low"]=(h4_["close"]-h4_["low"])/(h4_["high"]-h4_["low"]+0.001)<0.15
    longs=h4_[h4_["_vc"]&h4_["_near_high"]].index
    shorts=h4_[h4_["_vc"]&h4_["_near_low"]].index
    return longs, shorts

def test_vc(h4_,d1_,longs,shorts,label):
    """Test VC signals with various filters"""
    all_sigs=pd.Index(longs).union(shorts)
    print(f"\n--- {label}: {len(longs)}L + {len(shorts)}S = {len(all_sigs)} sigs ---")
    
    # Base WR
    results=[]
    for idx in all_sigs:
        il=idx in longs
        loc=h4_.index.get_loc(idx)
        if loc+1<len(h4_):
            r=(h4_.iloc[loc+1]["close"]-h4_.iloc[loc]["close"])/h4_.iloc[loc]["close"]
            w=(r>0) if il else (r<0)
            results.append({"t":idx,"d":"L" if il else "S","r":r,"w":w})
    if not results: return
    bwr=sum(r["w"] for r in results)/len(results)
    brt=np.mean([r["r"] for r in results])
    print(f"  BASE: WR={bwr*100:.1f}% ({sum(r['w'] for r in results)}/{len(results)}), AVG={brt*100:.2f}%")
    
    def get_z(d_,t_,col):
        d=pd.Timestamp(t_.date())
        return d_.loc[d,col] if d in d_.index else np.nan
    
    # Test: FIZ bias filter (YUR z-score is best filter)
    for zcol,zlabel in [(f"yur_z_{w}",f"YUR_{w}d") for w in [10,20,30]]+[(f"fiz_z_{w}",f"FIZ_{w}d") for w in [10,20,30]]:
        for zt in [0.5,1.0,1.5]:
            flt=[]
            for r in results:
                z=get_z(d1_,r["t"],zcol)
                if not np.isnan(z) and z>0:
                    flt_dir="L"
                elif not np.isnan(z) and z<0:
                    flt_dir="S"
                else:
                    continue
                if r["d"]==flt_dir:
                    flt.append(r)
            if flt and len(flt)>=10:
                wr=sum(r["w"] for r in flt)/len(flt)
                rt=np.mean([r["r"] for r in flt])
                print(f"  {zlabel} dir: {len(flt)} sig, WR={wr*100:.1f}%, AVG={rt*100:.2f}%")
            # Also extreme filter
            flt2=[]
            for r in results:
                z=get_z(d1_,r["t"],zcol)
                if not np.isnan(z) and abs(z)>=zt and r["d"]==("L" if z>0 else "S"):
                    flt2.append(r)
            if flt2 and len(flt2)>=10:
                wr=sum(r["w"] for r in flt2)/len(flt2)
                rt=np.mean([r["r"] for r in flt2])
                print(f"  {zlabel}|z|>={zt:.1f} dir: {len(flt2)} sig, WR={wr*100:.1f}%, AVG={rt*100:.2f}%")
    
    # Anti-crowd
    for zcol,zlabel in [(f"fiz_z_{w}",f"FIZ_{w}d") for w in [10,20,30]]:
        for zt in [0.5,1.0,1.5,2.0]:
            anti=[]
            for r in results:
                z=get_z(d1_,r["t"],zcol)
                if np.isnan(z) or abs(z)<zt: continue
                anti_dir="S" if z>0 else "L"  # Against FIZ
                if r["d"]==anti_dir:
                    anti.append(r)
            if anti and len(anti)>=5:
                wr=sum(r["w"] for r in anti)/len(anti)
                rt=np.mean([r["r"] for r in anti])
                print(f"  ANTI {zlabel}|z|>={zt:.1f}: {len(anti)} sig, WR={wr*100:.1f}%, AVG={rt*100:.2f}%")

# Test 1: Classic VC (vol > 2*median)
l1,s1=vc_sigs(h4,"volume",2*h4["vol_med20"])
test_vc(h4,d1,l1,s1,"VC vol>2*median + near extreme")

# Test 2: Looser VC (vol > 1.5*median)
l2,s2=vc_sigs(h4,"volume",1.5*h4["vol_med20"])
test_vc(h4,d1,l2,s2,"VC vol>1.5*median + near extreme")

# Test 3: VC with vol > mean+1.5*std
l3,s3=vc_sigs(h4,"volume",h4["vol_mean20"]+1.5*h4["vol_std20"])
test_vc(h4,d1,l3,s3,"VC vol>mean+1.5*std + near extreme")

# WHALE DETECTOR detailed
print("\n"+"="*70)
print("WHALE DETECTOR: Optimalniy threshold")
print("="*70)

# YUR z-score (best whale signal)
for w in [10,20,30]:
    print(f"\n  YUR z-score window={w}d:")
    for t in [1.0,1.5,2.0,2.5,3.0]:
        sig=d1[abs(d1[f"yur_z_{w}"])>t]
        wls=[]
        for idx in sig.index:
            loc=d1.index.get_loc(idx)
            if loc+1<len(d1):
                il=sig.loc[idx,f"yur_z_{w}"]>0
                ret=(d1.iloc[loc+1]["close"]-d1.iloc[loc]["close"])/d1.iloc[loc]["close"]
                wls.append((ret>0) if il else (ret<0))
        if wls:
            wr=sum(wls)/len(wls)*100
            print(f"    |z|>{t:.1f}: {len(sig)} sig, WR={wr:.1f}% ({sum(wls)}/{len(wls)})")

# WHALE vs VC frequency (FIXED)
print("\n"+"="*70)
print("WHALE vs VC FREQUENCY")
print("="*70)

# Get D1 dates with extreme YUR z-scores
for w in [20]:
    for t in [1.64,2.0,2.5]:
        wl=d1[abs(d1[f"yur_z_{w}"])>t]
        whale_days=set(wl.index.date)
        
        # Count H4 bars in whale days vs non-whale days
        all_d1_dates=set(d1.index.date)
        
        vc_on_wd=0
        vc_off_wd=0
        for idx in h4.index:
            d=idx.date()
            is_vc=idx in l1 or idx in s1
            if d in whale_days:
                if is_vc: vc_on_wd+=1
            else:
                if is_vc: vc_off_wd+=1
        
        h4_dates_whale=len([d for d in set(idx.date() for idx in h4.index) if d in whale_days])
        h4_dates_non=len([d for d in set(idx.date() for idx in h4.index) if d not in whale_days])
        
        print(f"\n  YUR|z|>{t} (window={w}d):")
        print(f"    Whale days: {len(whale_days)}, Non-whale: {len(all_d1_dates)-len(whale_days)}")
        print(f"    VC on whale: {vc_on_wd} ({vc_on_wd/max(1,h4_dates_whale):.3f}/d)")
        print(f"    VC on non: {vc_off_wd} ({vc_off_wd/max(1,h4_dates_non):.3f}/d)")

# FIZ vs YUR divergence
print("\n"+"="*70)
print("BONUS: FIZ vs YUR divergence -> fade FIZ")
print("="*70)

for w in [10,20,30]:
    print(f"\n  Window={w}d:")
    for t in [1.0,1.5,2.0]:
        d1["_div_z"]=d1[f"fiz_yur_diff_{w}"]
        sig=d1[abs(d1["_div_z"])>t]
        wls=[]
        for idx in sig.index:
            loc=d1.index.get_loc(idx)
            if loc+1<len(d1):
                div_z=sig.loc[idx,"_div_z"]
                # If fiz > yur (crowd buying more) -> fade fiz = short
                fade_dir="S" if div_z>0 else "L"
                ret=(d1.iloc[loc+1]["close"]-d1.iloc[loc]["close"])/d1.iloc[loc]["close"]
                wls.append((ret>0) if fade_dir=="L" else (ret<0))
        if wls and len(wls)>=5:
            wr=sum(wls)/len(wls)*100
            print(f"    |div|>{t:.1f}: {len(sig)} sig, WR={wr:.1f}% ({sum(wls)}/{len(wls)})")

print("\nDONE!")
