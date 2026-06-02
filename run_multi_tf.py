#!/usr/bin/env python3
"""Multi-timeframe OI backtest: D1 vs H4. Resamples price+OI together for H4."""
import sys, os, json, argparse
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2, numpy as np
from datetime import datetime, timezone, timedelta
import pandas as pd

W = 20

def load_data(sym, days=700):
    cn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, connect_timeout=10)
    sn = datetime.now(timezone.utc) - timedelta(days=days)
    df = pd.read_sql("SELECT time,open,high,low,close,volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s AND volume>0 ORDER BY time ASC", cn, params=(sym, sn))
    cn.close()
    if df.empty: return pd.DataFrame()
    df['time'] = df['time'].dt.floor('5min')
    return df

def load_oi_5m(sym, days=700):
    """Load intraday OI (5min resolution where available)."""
    cn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, connect_timeout=10)
    sn = datetime.now(timezone.utc) - timedelta(days=days)
    df = pd.read_sql("""
        SELECT time, clgroup, buy_accounts, sell_accounts, buy_orders, sell_orders
        FROM openinterest_moex WHERE symbol=%s AND buy_accounts>0 AND time>=%s
        ORDER BY time ASC
    """, cn, params=(sym, sn))
    cn.close()
    if df.empty: return pd.DataFrame()
    df['time'] = df['time'].dt.floor('5min')
    return df

def has_intraday_oi(sym):
    """Check if symbol has 5m OI or just daily."""
    cn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, connect_timeout=10)
    cur = cn.cursor()
    cur.execute("SELECT COUNT(*) FROM openinterest_moex WHERE symbol=%s", (sym,))
    cnt = cur.fetchone()[0]
    cn.close()
    return cnt > 10000  # 5m OI has 400k+, daily has 2-3k

def oi_pivot(oi_df):
    """Pivot OI from long (clgroup rows) to wide (fiz/yur columns)."""
    oi_df = oi_df.copy()
    oi_df['group'] = oi_df['clgroup'].map({0: 'fiz', 1: 'yur'})
    wide = oi_df.pivot_table(index='time', columns='group',
        values=['buy_accounts', 'sell_accounts', 'buy_orders', 'sell_orders'],
        aggfunc='first').fillna(0)
    wide.columns = [f"{m}_{g}" for m, g in wide.columns]
    for p in ['fiz', 'yur']:
        wide[f'{p}_long'] = wide.get(f'buy_orders_{p}', 0)
        wide[f'{p}_short'] = wide.get(f'sell_orders_{p}', 0)
        wide[f'{p}_lnum'] = wide.get(f'buy_accounts_{p}', 0)
        wide[f'{p}_snum'] = wide.get(f'sell_accounts_{p}', 0)
    return wide.reset_index()

def resample_tf(df_prices, oi_5m, tf, use_oi_intraday):
    """Resample prices and OI together to target timeframe."""
    df = df_prices.set_index('time')
    # Resample prices
    pdf = df.resample('1D' if tf == 'D1' else '4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna(subset=['open', 'close'])
    pdf = pdf[pdf['volume'] > 0]

    if use_oi_intraday and not oi_5m.empty:
        # Resample OI alongside prices
        odf = oi_5m.set_index('time').resample('1D' if tf == 'D1' else '4h').last().fillna(0)
        pdf = pdf.join(odf, how='inner')
    elif not oi_5m.empty:
        # Daily OI: join on date
        odf = oi_5m.set_index('time')
        odf_daily = odf.resample('1D').last().fillna(0)
        pdf = pdf.join(odf_daily, how='left').fillna(0)
        if tf == 'H4':
            # For H4 with daily OI: OI repeats across same-day bars
            pass  # join on date fills forward

    if tf == 'H4':
        pdf = pdf[(pdf.index.hour >= 4) & (pdf.index.hour < 21)]

    records = []
    for idx, row in pdf.iterrows():
        d = {"time": idx, "close": float(row['close']), "open": float(row.get('open', row['close'])),
             "high": float(row.get('high', row['close'])), "low": float(row.get('low', row['close'])),
             "volume": float(row.get('volume', 0))}
        if use_oi_intraday or not oi_5m.empty:
            for k in ['fiz_long', 'fiz_short', 'yur_long', 'yur_short',
                       'fiz_lnum', 'fiz_snum', 'yur_lnum', 'yur_snum']:
                d[k] = float(row.get(k, 0))
        records.append(d)
    return records

def compute_features(data, tf, has_oi=True):
    N, lp = len(data), {'short': 3, 'long': 5} if tf == 'D1' else {'short': 6, 'long': 10}
    if not has_oi:
        for i in range(N):
            d = data[i]
            for nl, n in [('_short', lp['short']), ('_long', lp['long'])]:
                if i >= n:
                    r = data[i - n]
                    d[f"price_d{nl}"] = (d['close'] - r['close']) / r['close'] * 100
                else:
                    d[f"price_d{nl}"] = 0
        for i, d in enumerate(data):
            for fp in [lp['short'], lp['long']]:
                fi = min(i + fp, N - 1)
                d[f"ret_{fp}"] = (data[fi]['close'] - d['close']) / d['close'] * 100
        return data

    ks = ["fiz_lnum", "fiz_snum", "yur_avg", "fiz_long_pct"]
    for i in range(N):
        d = data[i]
        d["yur_avg"] = d["yur_long"] / max(d["yur_lnum"], 1)
        d["fiz_long_pct"] = d["fiz_long"] / max(d["fiz_long"] + d["yur_long"], 1) * 100
        if i > 0:
            p = data[i-1]
            for k in ks: d[f"{k}_d1"] = d[k] - p[k]
            for nl, n in [('_short', lp['short']), ('_long', lp['long'])]:
                if i >= n:
                    r = data[i-n]
                    for k in ks: d[f"{k}_d{nl}"] = d[k] - r[k]
                    d[f"price_d{nl}"] = (d["close"] - r["close"]) / r["close"] * 100
                else:
                    for k in ks: d[f"{k}_d{nl}"] = 0; d[f"price_d{nl}"] = 0
        else:
            for k in ks: d[f"{k}_d1"] = 0
            for nl in ['_short', '_long']:
                for k in ks: d[f"{k}_d{nl}"] = 0; d[f"price_d{nl}"] = 0
        if i >= W:
            for k in ks:
                a = np.array([data[j][k] for j in range(i-W, i)])
                d[f"z_{k}"] = (d[k]-np.mean(a))/np.std(a) if np.std(a)>0 else 0
                d[f"pct_{k}"] = np.sum(a<d[k])/len(a)*100
            for k in ["fiz_lnum", "yur_avg", "fiz_long_pct"]:
                up=dn=0
                for j in range(i-1, max(i-15,0)-1, -1):
                    if data[j][k] < data[j+1][k]: up+=1
                    else: break
                for j in range(i-1, max(i-15,0)-1, -1):
                    if data[j][k] > data[j+1][k]: dn+=1
                    else: break
                d[f"{k}_up_streak"]=up; d[f"{k}_dn_streak"]=dn
        else:
            for k in ks: d[f"z_{k}"]=0; d[f"pct_{k}"]=50
            d[f"{k}_up_streak"]=d[f"{k}_dn_streak"]=0
    for i, d in enumerate(data):
        for fp in [lp['short'], lp['long']]:
            fi = min(i+fp, N-1)
            d[f"ret_{fp}"] = (data[fi]['close']-d['close'])/d['close']*100
    return data

def analyze(data, ms=3, dom=3.0, has_oi=True, h4_scale=None):
    if h4_scale is not None:
        return _analyze_h4(data, ms, dom, has_oi, h4_scale)
    lp = {'short':3,'long':5}
    BULLISH = []
    BEARISH = []
    if has_oi:
        BULLISH = [
            ("FIZ_DROP", lambda d: d.get("fiz_lnum_dn_streak",0)>=3),
            ("YUR_LOAD", lambda d: d.get("yur_avg_up_streak",0)>=5),
            ("FIZ_FLEE", lambda d: d.get("fiz_lnum_d_short",0)<-2000),
            ("FIZ_FLEE_ACCEL", lambda d: d.get("fiz_lnum_d_long",0)<-3000 and d.get("fiz_lnum_d_short",0)<d.get("fiz_lnum_d_long",0)*0.6),
            ("FIZ_PANIC_ACCEL", lambda d: d.get("fiz_snum_d_long",0)>2000 and d.get("fiz_snum_d_short",0)>d.get("fiz_snum_d_long",0)*0.6),
            ("FIZ_SHORT_SURGE", lambda d: d.get("fiz_short_d_long",0)>5000000),
            ("YUR_CALM_LOAD", lambda d: d.get("yur_avg_d_short",0)>0 and d.get("yur_avg_d_long",0)>0 and abs(d.get("fiz_lnum_d_short",0))<2000),
        ]
        BEARISH = [
            ("FIZ_EUPHORIA", lambda d: d.get("fiz_long_pct_up_streak",0)>=5),
            ("FALLING_KNIFE", lambda d: d.get("price_d_long",0)<-1.0 and d.get("fiz_lnum_d_long",0)>2000),
            ("RALLY_FLEE", lambda d: d.get("price_d_long",0)>1.0 and d.get("fiz_lnum_d_long",0)<-2000),
            ("FIZ_OVERLOAD", lambda d: d.get("pct_fiz_lnum",50)>=95 and d.get("price_d_short",0)>0.5),
            ("SHORT_SQZ_EXHAUST", lambda d: d.get("price_d_long",0)>1.0 and d.get("fiz_snum_d_long",0)>1000 and d.get("pct_fiz_snum",50)>=90),
        ]
    else:
        BULLISH = [
            ("MOMENTUM", lambda d: d.get("price_d_long",0)>1.0 and d.get("price_d_short",0)>0.5),
            ("BOUNCE", lambda d: d.get("price_d_long",0)<-2.0 and d.get("price_d_short",0)>0.3),
        ]
        BEARISH = [
            ("DROPS", lambda d: d.get("price_d_long",0)<-1.0 and d.get("price_d_short",0)<-0.5),
            ("TOP_BREAK", lambda d: d.get("price_d_long",0)>2.0 and d.get("price_d_short",0)<-0.3),
        ]

    sigs = []
    for i, d in enumerate(data[W:], start=W):
        bull = sum(1 for _, c in BULLISH if c(d))
        bear = sum(1 for _, c in BEARISH if c(d))
        if bull+bear < ms: continue
        ret_s = d.get("ret_3", d.get("ret_6", 0))
        ret_l = d.get("ret_5", d.get("ret_10", 0))
        if bull >= bear*dom:
            sigs.append({"time":str(d["time"]), "dir":"LONG", "ret_short":ret_s, "ret_long":ret_l, "hit":ret_s>0, "bull":bull, "bear":bear, "entry":d["close"]})
        elif bear >= bull*dom:
            sigs.append({"time":str(d["time"]), "dir":"SHORT", "ret_short":ret_s, "ret_long":ret_l, "hit":ret_s<0, "bull":bull, "bear":bear, "entry":d["close"]})
    return sigs

def _analyze_h4(data, ms, dom, has_oi, scale):
    vz = scale
    BULLISH, BEARISH = [], []
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

def run(sym, tf, days=700):
    print(f"  Load 5m...", end=" ", flush=True)
    df_p = load_data(sym, days)
    if df_p.empty or len(df_p) < 100: return None, f"No 5m data"
    print(f"{len(df_p)}b", end=" ")

    intraday_oi = has_intraday_oi(sym)
    oi_5m = pd.DataFrame()
    if intraday_oi:
        print(f"OI 5m...", end=" ", flush=True)
        oi_5m = load_oi_5m(sym, days)
        if not oi_5m.empty:
            oi_5m = oi_pivot(oi_5m)
            print(f"{len(oi_5m)}b", end=" ")
        else:
            intraday_oi = False

    print(f"Resample {tf}...", end=" ", flush=True)
    data = resample_tf(df_p, oi_5m, tf, intraday_oi)
    if len(data) < W + 20:
        return None, f"Only {len(data)} bars"
    print(f"{len(data)}b", end=" ")

    has_oi = intraday_oi or (not oi_5m.empty)
    # For Si daily OI on D1: has_oi=True. For Si on H4: has_oi=False (daily OI repeats, useless)
    # For other symbols: has_oi=True on both D1 and H4
    if tf == 'H4' and not intraday_oi:
        has_oi = False  # Si on H4 = price only

    print(f"Features...", end=" ", flush=True)
    data = compute_features(data, tf, has_oi)
    print(f"Analyze...", end=" ", flush=True)
    sigs = analyze(data, 3, 3.0, has_oi, h4_scale=0.3 if tf=="H4" else None)
    return data, sigs, has_oi

def summ(sigs):
    if not sigs: return {"n":0,"wins":0,"wr":0,"avg_ret_short":0,"total_pnl":0}
    w=sum(1 for s in sigs if s["hit"]); n=len(sigs); av=np.mean([s["ret_short"] for s in sigs])
    cum=sum((1 if s["dir"]=="LONG" else -1)*(s["ret_short"]/100) for s in sigs)
    return {"n":n,"wins":w,"wr":round(w/n*100,1) if n else 0,"avg_ret_short":round(av,2),"total_pnl":round(cum*100,2)}

CSS="""
body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:20px;max-width:1200px;margin:0 auto}
h1{color:#58a6ff;font-size:22px;margin-top:16px}h2{color:#f0883e;font-size:16px;margin-top:20px}
h3{color:#c9d1d9;font-size:14px;margin-top:14px}
table{border-collapse:collapse;margin:10px 0;width:100%;font-size:13px}
th,td{border:1px solid #30363d;padding:5px 10px;text-align:center}
th{background:#161b22;color:#8b949e;font-size:12px}td{background:#0d1117}
.best{background:rgba(63,185,80,0.08)!important}
.worst{background:rgba(248,81,73,0.08)!important}
.win{color:#3fb950;font-weight:600}.loss{color:#f85149;font-weight:600}
.num{font-variant-numeric:tabular-nums;text-align:right}
.tf-d1,.tf-D1{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600;background:#1f6feb;color:#fff}
.tf-h4,.tf-H4{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600;background:#d29922;color:#000}
.note{color:#8b949e;font-size:12px;font-style:italic;margin-left:8px}
"""

def gh(results):
    p = [f'<!DOCTYPE html><html><head><meta charset="utf-8"><script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script><style>{CSS}</style></head><body><h1>&#128202; Multi-TF: D1 vs H4</h1>']
    # Summary
    p.append('<h2>OI Resolution by Ticker</h2><table><tr><th>Sym</th><th>D1 OI</th><th>H4 OI</th></tr>')
    for sym in sorted(results.keys()):
        d1_oi = "5m &#9989;" if results[sym].get('d1_oi_intraday') else ("Daily &#9898;" if results[sym].get('d1_has_oi') else "&#10060;")
        h4_oi = "5m &#9989;" if results[sym].get('h4_oi_intraday') else ("Price only &#9874;" if results[sym].get('h4_has_oi') is False else "&#10060;")
        p.append(f'<tr><td><b>{sym}</b></td><td>{d1_oi}</td><td>{h4_oi}</td></tr>')
    p.append('</table>')

    p.append('<h2>Per Ticker</h2><table><tr><th>Sym</th><th colspan=4>D1</th><th colspan=4>H4</th><th>WR &#916;</th></tr>')
    p.append('<tr><th></th><th>Sig</th><th>WR</th><th>AvgR</th><th>PnL</th><th>Sig</th><th>WR</th><th>AvgR</th><th>PnL</th></tr>')
    ts1,tw1,ts2,tw2=0,0,0,0
    for sym in sorted(results.keys()):
        r=results[sym]; d1=summ(r.get('d1',[])); h4=summ(r.get('h4',[]))
        def cc(s):
            if not s['n']: return "<td>-</td><td>-</td><td>-</td><td>-</td>"
            cl='win' if s['avg_ret_short']>0 else 'loss'
            return f'<td>{s["n"]}</td><td>{s["wr"]}%</td><td class="{cl} num">{s["avg_ret_short"]:+.2f}%</td><td class="num">{s["total_pnl"]:+.1f}%</td>'
        wd=h4['wr']-d1['wr'] if d1['n'] and h4['n'] else 0
        wc='best' if wd>0 else ('worst' if wd<0 else '')
        p.append(f'<tr><td><b>{sym}</b></td>{cc(d1)}{cc(h4)}<td class="{wc}">{wd:+.1f}%</td></tr>')
        ts1+=d1['n'];tw1+=d1['wins'];ts2+=h4['n'];tw2+=h4['wins']
    wr1=round(tw1/ts1*100,1) if ts1 else 0; wr2=round(tw2/ts2*100,1) if ts2 else 0
    p.append(f'<tr style="font-weight:bold;border-top:2px solid #58a6ff"><td>TOTAL</td><td>{ts1}</td><td>{wr1}%</td><td>-</td><td>-</td><td>{ts2}</td><td>{wr2}%</td><td>-</td><td>-</td><td>{wr2-wr1:+.1f}%</td></tr></table>')
    # Detail per ticker
    for sym in sorted(results.keys()):
        r=results[sym]
        for tf,lab,col in [('d1','D1','#1f6feb'),('h4','H4','#d29922')]:
            sigs=r.get(tf,[])
            if not sigs: continue
            s=summ(sigs)
            data=r.get(f'{tf}_data',[]); ntf=tf
            if not data: continue
            dates=[str(d['time']) for d in data]; prices=[d['close'] for d in data]
            sd=[sg['time'] for sg in sigs]; sp=[sg['entry'] for sg in sigs]
            se=json.dumps([dict(sg) for sg in sigs])
            has_oi = r.get(f'{tf}_has_oi', True)
            oi_note = '' if has_oi else ' <span class="note">(price only)</span>'
            p.append(f'<h3>{sym} <span class="tf-{ntf}">{lab}</span> &mdash; {s["n"]} sig, {s["wr"]}% WR, avg {s["avg_ret_short"]:+.2f}%{oi_note}</h3><div id="ch_{sym}_{ntf}"></div>')
            p.append(f'<script>var d={se};Plotly.newPlot("ch_{sym}_{ntf}",[{{x:{json.dumps(dates)},y:{json.dumps(prices)},type:"scatter",mode:"lines",line:{{color:"{col}",width:1.5}},name:"{lab}"}},{{x:{json.dumps(sd)},y:{json.dumps(sp)},type:"scatter",mode:"markers",marker:{{size:d.map(function(s){{return s.hit?10:6}}),color:d.map(function(s){{return s.dir==="LONG"?"#3fb950":"#f85149"}}),symbol:d.map(function(s){{return s.dir==="LONG"?"triangle-up":"triangle-down"}}),line:{{width:1,color:"#fff"}}}},text:d.map(function(s){{return s.time+" "+s.dir+" ret="+s.ret_short.toFixed(1)+"% "+(s.hit?"\u2705":"\u274c")}}),hoverinfo:"text",name:"Sig"}}],{{paper_bgcolor:"#0d1117",plot_bgcolor:"#0d1117",font:{{color:"#c9d1d9"}},xaxis:{{gridcolor:"#21262d"}},yaxis:{{gridcolor:"#21262d",zerolinecolor:"#30363d"}},margin:{{l:60,r:30,t:30,b:50}},height:350,hovermode:"x unified"}});</script>')
            p.append('<table style="font-size:12px"><tr><th>Time</th><th>Dir</th><th>Entry</th><th>RetS</th><th>RetL</th><th>B</th><th>S</th><th>Res</th></tr>')
            for sg in sigs:
                cl='win' if sg['hit'] else 'loss'
                p.append(f'<tr><td>{sg["time"][:19]}</td><td>{sg["dir"]}</td><td class="num">{sg["entry"]:.0f}</td><td class="num {cl}">{sg["ret_short"]:+.2f}%</td><td class="num">{sg["ret_long"]:+.2f}%</td><td>{sg["bull"]}</td><td>{sg["bear"]}</td><td class="{cl}">{"\u2705" if sg["hit"] else "\u274c"}</td></tr>')
            p.append('</table>')
    p.append('</body></html>'); return "\n".join(p)

def main():
    a=argparse.ArgumentParser(); a.add_argument("--tickers",nargs="+",default=['Si','MX','SR','GD','RI','LK']); a.add_argument("--days",type=int,default=700)
    args=a.parse_args()
    results={}
    for sym in args.tickers:
        print(f"\n{'='*60}\n{sym}")
        sr = {}
        for tf in ['D1', 'H4']:
            print(f" [{tf}]", end=" ", flush=True)
            data, sigs, has_oi = run(sym, tf, args.days)
            if data is None:
                print(f"SKIP: {sigs}")
                if sigs: print(f"  {sigs}")
                continue
            s = summ(sigs)
            oi_type = "has intraday OI" if has_oi else ("price only" if has_oi is False else "daily OI")
            print(f'{s["n"]} sig, {s["wr"]}% WR, avg {s["avg_ret_short"]:+.2f}% ({oi_type})')
            sr[tf.lower()] = sigs; sr[f'{tf.lower()}_data'] = data; sr[f'{tf.lower()}_has_oi'] = has_oi
            sr[f'{tf.lower()}_oi_intraday'] = has_oi
        results[sym] = sr
    html = gh(results)
    out = "/home/user/projects/TQA-MOEX/multi_tf_comparison.html"
    with open(out, "w") as f: f.write(html)
    import shutil; shutil.copy(out, "/home/user/.hermes/image_cache/multi_tf_comparison.html")
    print(f"\n{'='*60}\nReport: {out}\nhttp://10.0.0.60:5055/multi_tf_comparison.html")

if __name__ == "__main__":
    main()
