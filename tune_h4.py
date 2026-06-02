#!/usr/bin/env python3
"""Tune H4 thresholds: find optimal scale for OI/price thresholds."""
import sys
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from run_multi_tf import load_data, load_oi_5m, oi_pivot, resample_tf, compute_features, summ, W

def analyze_scale(data, ms, dom, has_oi, scale):
    vz = scale
    if has_oi:
        BULLISH = [
            ("FIZ_DROP", lambda d: d.get("fiz_lnum_dn_streak",0)>=3),
            ("YUR_LOAD", lambda d: d.get("yur_avg_up_streak",0)>=5),
            ("FIZ_FLEE", lambda d: d.get("fiz_lnum_d_short",0)<-2000*vz),
            ("FIZ_FLEE_ACCEL", lambda d: d.get("fiz_lnum_d_long",0)<-3000*vz and d.get("fiz_lnum_d_short",0)<d.get("fiz_lnum_d_long",0)*0.6),
            ("FIZ_PANIC_ACCEL", lambda d: d.get("fiz_snum_d_long",0)>2000*vz and d.get("fiz_snum_d_short",0)>d.get("fiz_snum_d_long",0)*0.6),
            ("FIZ_SHORT_SURGE", lambda d: d.get("fiz_short_d_long",0)>5000000*vz),
            ("YUR_CALM_LOAD", lambda d: d.get("yur_avg_d_short",0)>0 and d.get("yur_avg_d_long",0)>0 and abs(d.get("fiz_lnum_d_short",0))<2000*vz),
        ]
        BEARISH = [
            ("FIZ_EUPHORIA", lambda d: d.get("fiz_long_pct_up_streak",0)>=5),
            ("FALLING_KNIFE", lambda d: d.get("price_d_long",0)<-1.0*vz and d.get("fiz_lnum_d_long",0)>2000*vz),
            ("RALLY_FLEE", lambda d: d.get("price_d_long",0)>1.0*vz and d.get("fiz_lnum_d_long",0)<-2000*vz),
            ("FIZ_OVERLOAD", lambda d: d.get("pct_fiz_lnum",50)>=95 and d.get("price_d_short",0)>0.5*vz),
            ("SHORT_SQZ_EXHAUST", lambda d: d.get("price_d_long",0)>1.0*vz and d.get("fiz_snum_d_long",0)>1000*vz and d.get("pct_fiz_snum",50)>=90),
        ]
    else:
        BULLISH = [("MOMENTUM", lambda d: d.get("price_d_long",0)>1.0 and d.get("price_d_short",0)>0.5),("BOUNCE", lambda d: d.get("price_d_long",0)<-2.0 and d.get("price_d_short",0)>0.3)]
        BEARISH = [("DROPS", lambda d: d.get("price_d_long",0)<-1.0 and d.get("price_d_short",0)<-0.5),("TOP_BREAK", lambda d: d.get("price_d_long",0)>2.0 and d.get("price_d_short",0)<-0.3)]
    sigs = []
    for i,d in enumerate(data[W:], start=W):
        bull=sum(1 for _,c in BULLISH if c(d)); bear=sum(1 for _,c in BEARISH if c(d))
        if bull+bear<ms: continue
        rs=d.get("ret_3",d.get("ret_6",0)); rl=d.get("ret_5",d.get("ret_10",0))
        if bull>=bear*dom: sigs.append({"time":str(d["time"]),"dir":"LONG","ret_short":rs,"ret_long":rl,"hit":rs>0,"bull":bull,"bear":bear,"entry":d["close"]})
        elif bear>=bull*dom: sigs.append({"time":str(d["time"]),"dir":"SHORT","ret_short":rs,"ret_long":rl,"hit":rs<0,"bull":bull,"bear":bear,"entry":d["close"]})
    return sigs

def run_tune(sym,tf,days):
    df_p=load_data(sym,days)
    if df_p.empty or len(df_p)<100: return None,None
    intraday=True
    for sym in [sym]: pass
    oi_df=load_oi_5m(sym,days)
    oi_5m=oi_pivot(oi_df) if not oi_df.empty else None
    data=resample_tf(df_p,oi_5m,tf,intraday and oi_5m is not None)
    if len(data)<W+20: return None,None
    data=compute_features(data,tf)
    has_oi=(oi_5m is not None and not oi_5m.empty)
    if tf=="H4" and not intraday: has_oi=False
    return data,has_oi

if __name__=="__main__":
    tickers=["MX","SR","GD","RI","LK"]
    print("="*60)
    print("D1 BASELINE")
    for sym in tickers:
        data,has_oi=run_tune(sym,"D1",700)
        if data is None: continue
        sigs=analyze_scale(data,3,3.0,has_oi,1.0)
        s=summ(sigs)
        print(f"  {sym}: {s['n']} sig, {s['wr']}% WR, avg {s['avg_ret_short']:+.2f}%")
    print("\n"+"="*60)
    print("H4 TUNING")
    print(f"{'T':>6} {'Scl':>5} {'MS':>3} {'Sig':>4} {'WR':>6} {'AvgR':>8} {'PnL':>8}")
    print("-"*46)
    for sym in tickers:
        data,has_oi=run_tune(sym,"H4",700)
        if data is None: continue
        configs=[]
        for scale in [0.1,0.2,0.3,0.5,0.7,1.0,1.5]:
            for ms in [2,3]:
                sigs=analyze_scale(data,ms,3.0,has_oi,scale)
                s=summ(sigs)
                if s["n"]>=3: configs.append({"s":scale,"m":ms,"n":s["n"],"w":s["wr"],"a":s["avg_ret_short"],"p":s["total_pnl"]})
        configs.sort(key=lambda x:(-x["w"],-x["n"]))
        if configs:
            for r in configs[:5]:
                print(f"  {sym:>6} {r['s']:>5.1f} {r['m']:>3d} {r['n']:>4d} {r['w']:>5.1f}% {r['a']:>+7.2f}% {r['p']:>+7.1f}%")
        else:
            print(f"  {sym:>6} {'-':>5} {'-':>3} {'-':>4} {'-':>6} {'-':>8} {'-':>8}")
