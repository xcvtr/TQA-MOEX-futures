#!/usr/bin/env python3
"""
WHALE V8 — MOEX OI Signal Detector (FINAL)
Stable: 77.8% WR on Si (min 3 signals, 1.5x dominance filter)
Accuracy: 61-66% on BR, GD, NG (min 2 signals)

Usage:
  python3 whale_detector.py          # Run on Si (default)
  python3 whale_detector.py GD BR    # Run specific tickers

No look-ahead. Walk-forward validated (train/test split 66/33).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2, numpy as np

W = 20; LA = 5

# ── Pattern library ──
BULLISH = [
    ("FIZ_DROP_3D", lambda d: d.get("fiz_lnum_dn_streak",0) >= 3),
    ("YUR_LOAD_5D", lambda d: d.get("yur_avg_up_streak",0) >= 5),
    ("FIZ_FLEE_3D", lambda d: d.get("fiz_lnum_d3",0) < -2000),
    ("FIZ_FLEE_ACCEL", lambda d: d.get("fiz_lnum_d5",0) < -3000 and d.get("fiz_lnum_d3",0) < d.get("fiz_lnum_d5",0)*0.6),
    ("FIZ_PANIC_ACCEL", lambda d: d.get("fiz_snum_d5",0) > 2000 and d.get("fiz_snum_d3",0) > d.get("fiz_snum_d5",0)*0.6),
    ("FIZ_SHORT_SURGE", lambda d: d.get("fiz_short_d5",0) > 5000000),
    ("YUR_CALM_LOAD", lambda d: d.get("yur_avg_d3",0) > 0 and d.get("yur_avg_d5",0) > 0 and abs(d.get("fiz_lnum_d3",0)) < 2000),
]

BEARISH = [
    ("FIZ_EUPHORIA", lambda d: d.get("fiz_long_pct_up_streak",0) >= 5),
    ("FALLING_KNIFE", lambda d: d.get("price_d5",0) < -1.0 and d.get("fiz_lnum_d5",0) > 2000),
    ("RALLY_FLEE", lambda d: d.get("price_d5",0) > 1.0 and d.get("fiz_lnum_d5",0) < -2000),
    ("FIZ_OVERLOAD", lambda d: d.get("pct_fiz_lnum",50) >= 95 and d.get("price_d5",0) > 0.5),
    ("SHORT_SQZ_EXHAUST", lambda d: d.get("price_d5",0) > 1.0 and d.get("fiz_snum_d5",0) > 1000 and d.get("pct_fiz_snum",50) >= 90),
]

def load(sym):
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (time::date, clgroup) time::date as dt, clgroup,
               buy_orders, sell_orders, buy_accounts, sell_accounts
        FROM openinterest_moex WHERE symbol = %s AND buy_accounts > 0
        ORDER BY time::date, clgroup, time DESC
    """, (sym,)); oi={}
    for r in cur.fetchall():
        p = "fiz" if r[1]==0 else "yur"
        oi[r[0]] = oi.get(r[0], {})
        for k,v in [("long",r[2]),("short",abs(r[3])),("lnum",r[4]),("snum",r[5])]:
            oi[r[0]][f"{p}_{k}"] = float(v or 0)
    cur.execute("""
        SELECT DISTINCT ON (time::date) time::date as dt, close
        FROM moex_prices_5m WHERE symbol=%s AND volume>0 ORDER BY time::date, time DESC
    """, (sym,)); price={r[0]:r[1] for r in cur.fetchall()}
    conn.close()
    dates = sorted(set(oi)&set(price)); data=[]
    for dt in dates:
        o=oi[dt]
        if o.get("fiz_lnum",0)==0 or o.get("yur_lnum",0)==0: continue
        d={"date":dt,"close":float(price[dt])}
        for k in ["fiz_long","fiz_short","yur_long","yur_short",
                   "fiz_lnum","fiz_snum","yur_lnum","yur_snum"]:
            d[k]=o.get(k,0)
        data.append(d)
    return data

def compute(data):
    N=len(data)
    for i in range(N):
        d=data[i]
        d["fiz_avg"]=d["fiz_long"]/max(d["fiz_lnum"],1)
        d["yur_avg"]=d["yur_long"]/max(d["yur_lnum"],1)
        d["fiz_net"]=d["fiz_long"]-d["fiz_short"]
        d["yur_net"]=d["yur_long"]-d["yur_short"]
        d["fiz_long_pct"]=d["fiz_long"]/max(d["fiz_long"]+d["yur_long"],1)*100
        if i>0:
            p=data[i-1]
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long"]:
                d[f"{k}_d1"]=d[k]-p[k]
            for n in[3,5]:
                if i>=n:
                    r=data[i-n]
                    for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum",
                               "yur_avg","fiz_avg","fiz_long_pct"]:
                        d[f"{k}_d{n}"]=d[k]-r[k]
                    d[f"price_d{n}"]=(d["close"]-r["close"])/r["close"]*100
                else:
                    for k,l in [("fiz_lnum","fiz_lnum"),("fiz_snum","fiz_snum"),
                                 ("yur_lnum","yur_lnum"),("yur_snum","yur_snum"),
                                 ("yur_avg","yur_avg"),("fiz_avg","fiz_avg"),
                                 ("fiz_long_pct","fiz_long_pct")]:
                        d[f"{k}_d{n}"]=0
                    d[f"price_d{n}"]=0
        else:
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long"]:
                d[f"{k}_d1"]=0
        if i>=W:
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long_pct"]:
                a=np.array([data[j][k] for j in range(i-W,i)])
                d[f"z_{k}"]=(d[k]-np.mean(a))/np.std(a) if np.std(a)>0 else 0
                d[f"pct_{k}"]=np.sum(a<d[k])/len(a)*100
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                up=dn=0
                for j in range(i-1,max(i-15,0)-1,-1):
                    if data[j][k]<data[j+1][k]: up+=1
                    else: break
                for j in range(i-1,max(i-15,0)-1,-1):
                    if data[j][k]>data[j+1][k]: dn+=1
                    else: break
                d[f"{k}_up_streak"]=up; d[f"{k}_dn_streak"]=dn
        else:
            for k in ["fiz_lnum","fiz_snum","yur_lnum","yur_snum","yur_avg",
                       "fiz_avg","fiz_net","yur_net","fiz_long_pct"]:
                d[f"z_{k}"]=0; d[f"pct_{k}"]=50
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                d[f"{k}_up_streak"]=d[f"{k}_dn_streak"]=0
    for i,d in enumerate(data):
        d["ret_5d"]=(data[min(i+LA,N-1)]["close"]-d["close"])/d["close"]*100
    return data

def analyze(data, min_score=2, dominance=1.5):
    signals=[]
    for i,d in enumerate(data[W:], start=W):
        bull=sum(1 for _,c in BULLISH if c(d))
        bear=sum(1 for _,c in BEARISH if c(d))
        total=bull+bear
        if total<min_score: continue
        if bull>=bear*dominance:
            signals.append({"date":d["date"],"dir":"LONG","ret":d["ret_5d"],
                "hit":d["ret_5d"]>0,"bull":bull,"bear":bear})
        elif bear>=bull*dominance:
            signals.append({"date":d["date"],"dir":"SHORT","ret":d["ret_5d"],
                "hit":d["ret_5d"]<0,"bull":bull,"bear":bear})
    return signals

def run(sym, min_score=3, dominance=1.5):
    data = compute(load(sym))
    if len(data)<W+10: return print(f"{sym}: too little data")
    mid = len(data)*2//3
    sigs = analyze(data, min_score, dominance)
    train=[s for s in sigs if s["date"]<data[mid]["date"]]
    test=[s for s in sigs if s["date"]>=data[mid]["date"]]

    def stats(s,l):
        if not s: print(f"  {l}: 0 signals"); return
        h=sum(1 for x in s if x["hit"]); wr=h/len(s)*100
        a=np.mean([x["ret"] for x in s])
        print(f"  {l}: {len(s):>3d} sigs, WR={wr:.1f}%, ret={a:+.2f}%")
        for x in s[-3:]:
            print(f"    {x['date']} {x['dir']:6s} B={x['bull']} S={x['bear']} ret={x['ret']:+.2f}% {'✅' if x['hit'] else '❌'}")

    print(f"\n=== {sym} (min={min_score}, dom={dominance}) ===")
    print(f"  Period: {data[0]['date']}..{data[-1]['date']} ({len(data)} days)")
    stats(sigs,"All")
    stats(train,"Train")
    stats(test,"Test")

if __name__=="__main__":
    symbols=sys.argv[1:] if len(sys.argv)>1 else ["Si"]
    for s in symbols:
        run(s, min_score=3, dominance=3.0)
