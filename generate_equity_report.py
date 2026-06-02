#!/usr/bin/env python3
"""Generate equity curves and trade charts for Whale Detector V8 report."""
import sys, os, json
sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
import psycopg2, numpy as np

W = 20

def load_clean(symbol):
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (time::date, clgroup) time::date as dt, clgroup,
               buy_orders, sell_orders, buy_accounts, sell_accounts
        FROM openinterest_moex WHERE symbol = %s AND buy_accounts > 0
        ORDER BY time::date, clgroup, time DESC
    """, (symbol,)); oi={}
    for r in cur.fetchall():
        p = "fiz" if r[1]==0 else "yur"
        oi[r[0]] = oi.get(r[0], {})
        for k,v in [("long",r[2]),("short",abs(r[3])),("lnum",r[4]),("snum",r[5])]:
            oi[r[0]][f"{p}_{k}"] = float(v or 0)
    cur.execute("""
        SELECT time::date as dt, close
        FROM moex_prices_5m WHERE symbol=%s AND volume>0 ORDER BY time::date, time DESC
    """, (symbol,))
    # Get first close of each day
    price = {}
    for r in cur.fetchall():
        if r[0] not in price:
            price[r[0]] = float(r[1])
    conn.close()
    dates = sorted(set(oi)&set(price)); data=[]
    for dt in dates:
        o=oi[dt]
        if o.get("fiz_lnum",0)==0 or o.get("yur_lnum",0)==0: continue
        d={"date":dt,"close":price[dt]}
        for k in ["fiz_long","fiz_short","yur_long","yur_short",
                   "fiz_lnum","fiz_snum","yur_lnum","yur_snum"]:
            d[k]=o.get(k,0)
        data.append(d)
    return data

def compute(data):
    N=len(data)
    for i in range(N):
        d=data[i]
        d["yur_avg"]=d["yur_long"]/max(d["yur_lnum"],1)
        d["fiz_long_pct"]=d["fiz_long"]/max(d["fiz_long"]+d["yur_long"],1)*100
        if i>0:
            p=data[i-1]
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                d[f"{k}_d1"]=d[k]-p[k]
            for n in[3,5]:
                if i>=n:
                    r=data[i-n]
                    for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                        d[f"{k}_d{n}"]=d[k]-r[k]
                    d[f"price_d{n}"]=(d["close"]-r["close"])/r["close"]*100
                else:
                    for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                        d[f"{k}_d{n}"]=0
                    d[f"price_d{n}"]=0
        else:
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                d[f"{k}_d1"]=0
        if i>=W:
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                a=np.array([data[j][k] for j in range(i-W,i)])
                d[f"z_{k}"]=(d[k]-np.mean(a))/np.std(a) if np.std(a)>0 else 0
                d[f"pct_{k}"]=np.sum(a<d[k])/len(a)*100
            for k in ["fiz_lnum","yur_avg","fiz_long_pct"]:
                up=dn=0
                for j in range(i-1,max(i-15,0)-1,-1):
                    if data[j][k] < data[j+1][k]:
                        up += 1
                    else:
                        break
                for j in range(i-1,max(i-15,0)-1,-1):
                    if data[j][k] > data[j+1][k]:
                        dn += 1
                    else:
                        break
                d[f"{k}_up_streak"]=up; d[f"{k}_dn_streak"]=dn
        else:
            for k in ["fiz_lnum","fiz_snum","yur_avg","fiz_long_pct"]:
                d[f"z_{k}"]=0; d[f"pct_{k}"]=50
                d[f"{k}_up_streak"]=d[f"{k}_dn_streak"]=0
    for i,d in enumerate(data):
        d["ret_5d"]=(data[min(i+5,N-1)]["close"]-d["close"])/d["close"]*100
        d["ret_10d"]=(data[min(i+10,N-1)]["close"]-d["close"])/d["close"]*100
    return data

# Patterns
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

def analyze(data, min_score=3, dominance=3.0):
    sigs = []
    for i,d in enumerate(data[W:], start=W):
        bull=sum(1 for _,c in BULLISH if c(d))
        bear=sum(1 for _,c in BEARISH if c(d))
        total=bull+bear
        if total<min_score: continue
        if bull>=bear*dominance:
            sigs.append({"date":d["date"],"dir":"LONG","ret_5d":d["ret_5d"],"ret_10d":d["ret_10d"],
                "hit":d["ret_5d"]>0,"bull":bull,"bear":bear,"entry":d["close"],
                "exit_5d":None,"exit_10d":None})
        elif bear>=bull*dominance:
            sigs.append({"date":d["date"],"dir":"SHORT","ret_5d":d["ret_5d"],"ret_10d":d["ret_10d"],
                "hit":d["ret_5d"]<0,"bull":bull,"bear":bear,"entry":d["close"],
                "exit_5d":None,"exit_10d":None})
    return sigs

def generate_html(ticker_map):
    """Generate Plotly HTML with equity curves for all tickers."""
    all_equity = {}  # date -> cum_pnl
    
    for sym in ticker_map:
        data = compute(load_clean(sym))
        if len(data) < W + 10: continue
        sigs = analyze(data, 3, 3.0)
        ticker_map[sym]["sigs"] = sigs
        ticker_map[sym]["data"] = data
        
        # Build price array and equity
        equity = {}
        cum = 0
        for s in sigs:
            d_idx = next(i for i, d in enumerate(data) if d["date"] == s["date"])
            mult = 1 if s["dir"] == "LONG" else -1
            # Use 5d return for pnl
            pnl = s["ret_5d"] / 100 * mult
            cum += pnl
            equity[s["date"]] = cum
        ticker_map[sym]["equity"] = equity
        
        for dt, val in equity.items():
            all_equity[dt] = all_equity.get(dt, 0) + val

    # Build HTML
    html = []
    html.append("""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:20px}
h1{color:#58a6ff}
h2{color:#f0883e}
table{border-collapse:collapse;margin:10px 0;width:100%}
th,td{border:1px solid #30363d;padding:6px 12px;text-align:center}
th{background:#161b22;color:#8b949e}
td{background:#0d1117}
.win{color:#3fb950}
.loss{color:#f85149}
.eq-pos{fill:#3fb950}
.eq-neg{fill:#f85149}
</style></head><body>
<h1>🐋 Whale Detector V8 — Equity Report</h1>
""")
    
    # Summary table
    html.append("<h2>Summary</h2><table><tr><th>Ticker</th><th>Signals</th><th>Wins</th><th>WR</th><th>Avg Ret 5d</th><th>Avg Ret 10d</th><th>Total PnL</th></tr>")
    for sym, info in sorted(ticker_map.items()):
        sigs = info.get("sigs", [])
        if not sigs: continue
        wins = sum(1 for s in sigs if s["hit"])
        wr = wins/len(sigs)*100
        avg5 = np.mean([s["ret_5d"] for s in sigs])
        avg10 = np.mean([s["ret_10d"] for s in sigs])
        total_pnl = list(info["equity"].values())[-1] if info.get("equity") else 0
        color = "win" if wr >= 70 else ""
        html.append(f'<tr class="{color}"><td>{sym}</td><td>{len(sigs)}</td><td>{wins}</td><td>{wr:.1f}%</td><td>{avg5:+.2f}%</td><td>{avg10:+.2f}%</td><td>{total_pnl:+.2f}%</td></tr>')
    
    # Combined total
    if all_equity:
        total_pnl = list(all_equity.values())[-1]
        html.append(f'<tr style="font-weight:bold;border-top:2px solid #58a6ff"><td>TOTAL</td><td>{sum(len(info.get("sigs",[])) for info in ticker_map.values())}</td><td>{sum(sum(1 for s in info.get("sigs",[]) if s["hit"]) for info in ticker_map.values())}</td><td>{sum(sum(1 for s in info.get("sigs",[]) if s["hit"]) for info in ticker_map.values())/max(sum(len(info.get("sigs",[])) for info in ticker_map.values()),1)*100:.1f}%</td><td colspan="2"></td><td>{total_pnl:+.2f}%</td></tr>')
    
    html.append("</table>")
    
    # === COMBINED EQUITY ===
    if all_equity:
        dates = sorted(all_equity.keys())
        vals = [all_equity[d] for d in dates]
        html.append(f"""
<h2>Combined Equity (all tickers)</h2>
<div id="equity_total"></div>
<script>
Plotly.newPlot('equity_total', [{{
    x: {json.dumps([str(d) for d in dates])},
    y: {json.dumps(vals)},
    type: 'scatter', mode: 'lines',
    line: {{color: '#58a6ff', width: 2}},
    fill: 'tozeroy',
    fillcolor: 'rgba(88,166,255,0.1)',
    name: 'Equity'
}}], {{
    paper_bgcolor: '#0d1117', plot_bgcolor: '#0d1117',
    font: {{color: '#c9d1d9'}},
    xaxis: {{title: 'Date', gridcolor: '#21262d'}},
    yaxis: {{title: 'Cumulative PnL (%)', gridcolor: '#21262d', zerolinecolor: '#30363d'}},
    margin: {{l:60,r:30,t:30,b:50}},
    hovermode: 'x unified'
}});
</script>
""")
    
    # === PER-TICKER ===
    for sym, info in sorted(ticker_map.items()):
        sigs = info.get("sigs", [])
        data = info.get("data", [])
        if not sigs or not data: continue
        
        # Price trace
        dates = [d["date"] for d in data]
        prices = [d["close"] for d in data]
        eq_dates = list(info["equity"].keys())
        eq_vals = [info["equity"][d] for d in eq_dates]
        
        # Signal markers
        sig_dates = [str(s["date"]) for s in sigs]
        sig_prices = [s["entry"] for s in sigs]
        sig_colors = ["#3fb950" if s["dir"]=="LONG" else "#f85149" for s in sigs]
        sig_symbols = ["triangle-up" if s["dir"]=="LONG" else "triangle-down" for s in sigs]
        sig_sizes = [12 if s["hit"] else 8 for s in sigs]
        sig_text = [f"{s['date']} {s['dir']} ret={s['ret_5d']:.1f}% B={s['bull']} S={s['bear']} {'✅' if s['hit'] else '❌'}" for s in sigs]
        
        wins = sum(1 for s in sigs if s["hit"])
        wr = wins/len(sigs)*100
        avg_ret = np.mean([s["ret_5d"] for s in sigs])
        
        html.append(f"""
<h2>{sym} — {len(sigs)} signals, {wr:.1f}% WR, avg ret {avg_ret:+.2f}%</h2>
<div id="chart_{sym}"></div>
<div id="equity_{sym}"></div>
<script>
Plotly.newPlot('chart_{sym}', [
    // Price line
    {{
        x: {json.dumps([str(d) for d in dates])},
        y: {json.dumps(prices)},
        type: 'scatter', mode: 'lines',
        line: {{color: '#c9d1d9', width: 1}},
        name: 'Price',
        yaxis: 'y'
    }},
    // Signal markers
    {{
        x: {json.dumps(sig_dates)},
        y: {json.dumps(sig_prices)},
        type: 'scatter', mode: 'markers',
        marker: {{
            size: {json.dumps(sig_sizes)},
            color: {json.dumps(sig_colors)},
            symbol: {json.dumps(sig_symbols)},
            line: {{width: 1, color: '#fff'}}
        }},
        text: {json.dumps(sig_text)},
        hoverinfo: 'text',
        name: 'Signals'
    }}
], {{
    paper_bgcolor: '#0d1117', plot_bgcolor: '#0d1117',
    font: {{color: '#c9d1d9'}},
    xaxis: {{title: 'Date', gridcolor: '#21262d'}},
    yaxis: {{title: 'Price', gridcolor: '#21262d', zerolinecolor: '#30363d'}},
    margin: {{l:60,r:30,t:30,b:50}},
    hovermode: 'x unified',
    height: 400,
    showlegend: true
}});

Plotly.newPlot('equity_{sym}', [{{
    x: {json.dumps([str(d) for d in eq_dates])},
    y: {json.dumps(eq_vals)},
    type: 'scatter', mode: 'lines',
    line: {{color: '#d29922', width: 2}},
    fill: 'tozeroy',
    fillcolor: 'rgba(210,153,34,0.1)',
    name: 'Equity'
}}], {{
    paper_bgcolor: '#0d1117', plot_bgcolor: '#0d1117',
    font: {{color: '#c9d1d9'}},
    xaxis: {{title: 'Date', gridcolor: '#21262d'}},
    yaxis: {{title: 'Cumulative PnL (%)', gridcolor: '#21262d', zerolinecolor: '#30363d'}},
    margin: {{l:60,r:30,t:30,b:50}},
    hovermode: 'x unified',
    height: 300
}});
</script>

<h3>Trade Log</h3>
<table><tr><th>Date</th><th>Dir</th><th>Entry</th><th>Ret 5d</th><th>Ret 10d</th><th>Patterns</th><th>Result</th></tr>
""")
        for s in sigs:
            patterns = []
            bnames = [n for n,c in BULLISH if c(data[next(i for i,d in enumerate(data) if d["date"]==s["date"])])]
            bearnames = [n for n,c in BEARISH if c(data[next(i for i,d in enumerate(data) if d["date"]==s["date"])])]
            pat_str = " ".join(bnames[:2]) + (" " if bnames and bearnames else "") + " ".join(bearnames[:2])
            result = "✅" if s["hit"] else "❌"
            html.append(f'<tr><td>{s["date"]}</td><td>{s["dir"]}</td><td>{s["entry"]:.0f}</td>'
                       f'<td>{s["ret_5d"]:+.2f}%</td><td>{s["ret_10d"]:+.2f}%</td>'
                       f'<td style="font-size:0.85em">{pat_str}</td><td class="{"win" if s["hit"] else "loss"}">{result}</td></tr>')
        
        html.append("</table>")
    
    html.append("</body></html>")
    return "\n".join(html)

if __name__ == "__main__":
    tickers = {
        "Si": {"name": "USD/RUB Futures"},
        "BR": {"name": "Brent Crude"},
        "GD": {"name": "Gold Futures"},
        "NG": {"name": "Natural Gas"},
    }
    
    html = generate_html(tickers)
    
    out_path = "/home/user/obsidian/Conversations/whale-v8-equity-report.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Report saved to {out_path}")
    
    # Also copy to image_cache for sending via MEDIA
    import shutil
    shutil.copy(out_path, "/home/user/.hermes/image_cache/whale-v8-equity-report.html")
    print("Copied to image_cache for delivery")
