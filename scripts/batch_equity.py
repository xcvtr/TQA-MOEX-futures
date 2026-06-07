#!/home/user/venvs/tqa/main/bin/python
"""Batch Equity Analysis -- 13 pairs x period, relaxed cluster detection.

Usage:
  python batch_equity.py                          # all 13, 2025
  python batch_equity.py --sym audjpy             # single pair
  python batch_equity.py --sym eurusd,gbpusd      # multi
  python batch_equity.py --threshold 1.0          # min cluster strength (lower=more)
  python batch_equity.py --max-clusters 10        # max per symbol
"""

import argparse, json, os, sys, warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

warnings.filterwarnings("ignore")

DB = dict(host="10.0.0.64", port=5432, dbname="forex", user="postgres", password="postgres")
OUTPUT_DIR = Path("/home/user/.hermes/cache/screenshots/tqa/")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["audjpy","audusd","euraud","eurgbp","eurjpy","eurusd",
           "gbpjpy","gbpusd","nzdusd","usdcad","usdchf","usdjpy","xauusd"]

JPY_PAIRS = {"audjpy","eurjpy","gbpjpy","usdjpy"}
XAU_PAIRS = {"xauusd"}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--sym", default=None)
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--max-clusters", type=int, default=10)
    p.add_argument("--threshold", type=float, default=1.5,
                   help="Spike threshold factor (1.0=median, 1.5=more clusters, 2.0=original)")
    p.add_argument("--min-vol", type=float, default=0.3,
                   help="Minimum single-day volume (lots)")
    p.add_argument("--price-band", type=float, default=10.0,
                   help="Price band as % of median (10 = +/-10%%)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Embedded find_clusters (relaxed)
# ---------------------------------------------------------------------------

def find_clusters_relaxed(conn, sym, start, end, prices,
                          max_clusters=10, threshold=1.5, min_vol=0.3, price_band_pct=10.0):
    """Rank-based cluster detection for positions data.

    Positions data is flat (no daily spikes) - each level has ~constant volume.
    Instead of spike detection, rank levels by total accumulated volume
    and use rolling windows for entry/exit timing.
    """
    p_median = prices['price'].median()
    band = price_band_pct / 100.0
    p_min = p_median * (1.0 - band)
    p_max = p_median * (1.0 + band)

    table = f"{sym}_dom"

    if sym in JPY_PAIRS:
        decimals = 1
    elif sym in XAU_PAIRS:
        decimals = 0
    else:
        decimals = 2

    # Load all DOM positions within price band
    dom = pd.read_sql(f"""
        SELECT time AT TIME ZONE 'UTC' as t,
               ROUND(price::numeric, {decimals}) as level,
               positions
        FROM {table}
        WHERE time >= '{start}' AND time < '{end}'
          AND positions IS NOT NULL AND positions != 0
          AND price >= {p_min} AND price <= {p_max}
        ORDER BY time
    """, conn)
    dom['t'] = pd.to_datetime(dom['t'], utc=True)

    if dom.empty:
        return []

    # Step 1: Find top levels by total accumulated positions
    level_stats = dom.groupby('level').agg(
        total_pos=('positions', lambda x: x.abs().sum()),
        net_pos=('positions', 'sum'),
        first_seen=('t', 'min'),
        last_seen=('t', 'max'),
        snapshots=('positions', 'count')
    ).reset_index()

    # Filter: need at least 10 snapshots to be meaningful
    level_stats = level_stats[level_stats['snapshots'] >= 10].copy()
    level_stats = level_stats.sort_values('total_pos', ascending=False)

    if level_stats.empty:
        return []

    # Step 2: Take top N unique levels (merge adjacent levels within proximity)
    if sym in JPY_PAIRS:
        zone_prox = 1.0
    elif sym in XAU_PAIRS:
        zone_prox = 5.0
    else:
        zone_prox = 0.004  # EURUSD: 1.0800, 1.0840 etc

    zones = []
    for _, row in level_stats.iterrows():
        cl = row['level']
        found = False
        for z in zones:
            if abs(cl - z['centroid']) < zone_prox:
                # Merge: prefer centroid weighted by total_pos
                old_v = z['total_pos']
                new_v = old_v + row['total_pos']
                z['centroid'] = (z['centroid'] * old_v + cl * row['total_pos']) / new_v
                z['total_pos'] = new_v
                z['net_pos'] = z['net_pos'] + row['net_pos']
                z['first_seen'] = min(z['first_seen'], row['first_seen'])
                z['last_seen'] = max(z['last_seen'], row['last_seen'])
                z['snapshots'] = z['snapshots'] + row['snapshots']
                z['levels'].append(cl)
                found = True
                break
        if not found:
            zones.append({
                'centroid': cl,
                'total_pos': row['total_pos'],
                'net_pos': row['net_pos'],
                'first_seen': row['first_seen'],
                'last_seen': row['last_seen'],
                'snapshots': row['snapshots'],
                'levels': [cl],
            })

    if not zones:
        return []

    # Step 3: Pick top zones, alternate long/short, cap at max_clusters
    pdata = prices.set_index('t')

    # Score: total_pos with recency bonus
    period_end = pd.to_datetime(end, utc=True)
    total_days = max(1, (period_end - pd.to_datetime(start, utc=True)).days)

    for z in zones:
        z_start = pd.to_datetime(z['first_seen'])
        if getattr(z_start, 'tz', None) is None:
            z_start = z_start.tz_localize('UTC')
        z_age = (period_end - z_start).total_seconds()
        recency = 1.0 + 0.3 * max(0, 1.0 - z_age / (total_days * 86400))
        z['score'] = z['total_pos'] * recency

    zones.sort(key=lambda x: x['score'], reverse=True)

    # Determine type from net_pos
    for z in zones:
        z['type'] = 'long' if z['net_pos'] > 0 else 'short'

    # Select with type alternation until max_clusters reached
    selected = []
    used_idx = set()
    targets = ['long', 'short']
    last_type = 'short'  # start with short so first pick is long
    while len(selected) < max_clusters:
        # Alternate type each pick
        if last_type == 'short':
            target = 'long'
        else:
            target = 'short'
        
        best_idx = None
        best_score = -1
        for i, z in enumerate(zones):
            if i in used_idx:
                continue
            if z['type'] != target:
                continue
            if best_idx is None or z['score'] > best_score:
                best_idx = i
                best_score = z['score']
        
        # If no more of this type, pick the best remaining of any type
        if best_idx is None:
            for i, z in enumerate(zones):
                if i in used_idx:
                    continue
                if best_idx is None or z['score'] > best_score:
                    best_idx = i
                    best_score = z['score']
        
        if best_idx is None:
            break  # no more zones at all
        
        selected.append(zones[best_idx])
        used_idx.add(best_idx)
        last_type = zones[best_idx]['type']

    # Step 4: Build cluster results with price entry/exit
    deduped = []
    for z in selected:
        centroid = round(z['centroid'], decimals)
        cluster_type = z['type']
        first_t = pd.to_datetime(z['first_seen'])
        last_t = pd.to_datetime(z['last_seen'])
        if getattr(first_t, 'tz', None) is None:
            first_t = first_t.tz_localize('UTC')
        if getattr(last_t, 'tz', None) is None:
            last_t = last_t.tz_localize('UTC')

        # Get price at first/last seen
        pc_idx = pdata.index.to_series().sub(first_t).abs().idxmin() if not pdata.empty else first_t
        pf_idx = pdata.index.to_series().sub(last_t).abs().idxmin() if not pdata.empty else last_t
        pc_val = float(pdata.loc[pc_idx, 'price']) if pc_idx in pdata.index else centroid
        pf_val = float(pdata.loc[pf_idx, 'price']) if pf_idx in pdata.index else centroid

        # Move in points
        if sym in JPY_PAIRS | XAU_PAIRS:
            mult = 100
        else:
            mult = 10000
        move_pts = round((pf_val - pc_val) * mult, 0)
        direction = 'up' if move_pts > 0 else 'down'

        deduped.append({
            "name": f"{cluster_type.upper()} {centroid:.{decimals}f}",
            "level": centroid,
            "color": "#3fb950" if cluster_type == 'long' else "#f85149",
            "start": str(first_t)[:19],
            "end": str(last_t)[:19],
            "peak_vol": round(float(z['total_pos']), 1),
            "total_vol": round(float(z['total_pos']), 1),
            "pc": round(pc_val, 5),
            "pf": round(pf_val, 5),
            "move": int(abs(move_pts)),
            "dir": direction,
        })

    return deduped


def calc_pnl(sym, cluster_type, start_price, end_price):
    if sym in XAU_PAIRS:
        mult = 100
    elif sym in JPY_PAIRS:
        mult = 100
    else:
        mult = 10000
    if cluster_type.upper() == "SHORT":
        return (start_price - end_price) * mult
    else:
        return (end_price - start_price) * mult


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def load_price(conn, sym, start, end):
    q = f"SELECT time AT TIME ZONE 'UTC' as t, price FROM {sym}_data WHERE time >= '{start}' AND time < '{end}' AND price > 0 ORDER BY time"
    df = pd.read_sql(q, conn)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df[["t", "price"]]


def generate_dashboard(all_results, start, end):
    total_pnl = sum(r["pnl"] for r in all_results.values())
    total_trades = sum(r["trades"] for r in all_results.values())
    total_wins = sum(r["wins"] for r in all_results.values())
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0

    fig = make_subplots(rows=3, cols=2,
        subplot_titles=("Equity Curve","Winrate","PnL","Trade Count","Avg PnL","Profit Factor"),
        specs=[[{"colspan":2},None],[{},{}],[{},{}]],
        vertical_spacing=0.08, horizontal_spacing=0.06)

    cumulative = 0
    dates, equity_line = [], []
    for sym in SYMBOLS:
        if sym not in all_results: continue
        for t in all_results[sym]["trades_list"]:
            cumulative += t["pnl"]
            dates.append(t.get("entry_time", sym))
            equity_line.append(cumulative)
    if dates:
        fig.add_trace(go.Scatter(x=dates, y=equity_line, mode="lines",
            line=dict(color="#58a6ff",width=2), fill="tozeroy",
            fillcolor="rgba(88,166,255,0.1)"), row=1, col=1)

    syms = [s for s in SYMBOLS if s in all_results]
    wr_v = [all_results[s]["wr"] for s in syms]
    pnl_v = [all_results[s]["pnl"] for s in syms]
    cnt_v = [all_results[s]["trades"] for s in syms]
    avg_v = [all_results[s]["avg_pnl"] for s in syms]
    pnl_c = ["#3fb950" if v >= 0 else "#f85149" for v in pnl_v]

    fig.add_trace(go.Bar(x=syms, y=wr_v, marker_color="#58a6ff", showlegend=False), row=2, col=1)
    fig.add_trace(go.Bar(x=syms, y=pnl_v, marker_color=pnl_c, showlegend=False), row=2, col=2)
    fig.add_trace(go.Bar(x=syms, y=cnt_v, marker_color="#d29922", showlegend=False), row=3, col=1)
    fig.add_trace(go.Bar(x=syms, y=avg_v, marker_color=pnl_c, showlegend=False), row=3, col=2)

    fig.update_layout(
        title=f"Equity Analysis -- {start[:10]} to {end[:10]}<br><sub>{total_trades} trades, WR {wr:.1f}%, Total PnL {total_pnl:+.0f}p</sub>",
        template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        font=dict(color="#e6edf3", size=10), height=900, hovermode="x unified",
        margin=dict(l=40,r=20,t=80,b=40))

    rows = []
    for sym in syms:
        r = all_results[sym]
        arrow = "\\U0001f7e2" if r["pnl"] >= 0 else "\\U0001f534"
        wr_c = "#3fb950" if r["wr"] >= 50 else "#f85149"
        rows.append(
            f"<tr><td class='sym'>{sym.upper()}</td>"
            f"<td>{r['trades']}</td>"
            f"<td class='num' style='color:{wr_c}'>{r['wr']:.0f}%</td>"
            f"<td class='num {'pos' if r['pnl'] >= 0 else 'neg'}'>{r['pnl']:+.0f}p</td>"
            f"<td class='num'>{r['pf']:.2f}</td>"
            f"<td class='num neg'>{r['dd']:.0f}p</td>"
            f"<td>{arrow}</td></tr>")

    pnl_color = "#3fb950" if total_pnl >= 0 else "#f85149"

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Equity Analysis {start[:10]}--{end[:10]}</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,sans-serif;padding:16px;max-width:1200px;margin:0 auto}}
h1{{font-size:1.3rem;color:#58a6ff;margin-bottom:2px}}
.sub{{color:#8b949e;font-size:0.8rem;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;font-size:0.75rem;margin:8px 0}}
th{{background:#21262d;color:#8b949e;padding:5px 8px;text-align:center;border:1px solid #30363d;font-weight:500}}
td{{padding:4px 8px;text-align:center;border:1px solid #21262d}}
.sym{{font-weight:600;color:#e6edf3;text-align:left}}
.num{{font-variant-numeric:tabular-nums}}
.pos{{color:#3fb950}} .neg{{color:#f85149}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;margin:8px 0}}
.card-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin:8px 0}}
.stat{{text-align:center}}
.stat .v{{font-size:1.3rem;font-weight:700}}
.stat .l{{font-size:0.7rem;color:#8b949e}}
.chart{{width:100%;height:900px}}
</style>
</head>
<body>
<h1>\\U0001f4ca Equity Analysis -- {start[:10]} to {end[:10]}</h1>
<p class="sub">{total_trades} trades across {len(syms)} symbols \\u2022 WR {wr:.1f}% \\u2022 Total PnL {total_pnl:+.0f}p</p>
<div class="card-grid">
  <div class="card stat"><div class="v" style="color:#58a6ff">{total_trades}</div><div class="l">Trades</div></div>
  <div class="card stat"><div class="v" style="color:#3fb950">{wr:.0f}%</div><div class="l">Winrate</div></div>
  <div class="card stat"><div class="v" style="color:{pnl_color}">{total_pnl:+.0f}</div><div class="l">Total PnL (p)</div></div>
</div>
<div class="card">{"<table><tr><th>Symbol</th><th>Trades</th><th>WR</th><th>PnL</th><th>PF</th><th>DD</th><th></th></tr>" + "".join(rows) + "</table>"}</div>
<div class="chart" id="chart"></div>
{fig.to_html(full_html=False, include_plotlyjs=False, div_id="chart")}
</body>
</html>"""
    return html


def analyze_symbol(conn, sym, start, end, max_clusters, threshold, min_vol, price_band):
    print(f"\\n{sym.upper()}:")
    prices = load_price(conn, sym, start, end)
    if len(prices) < 100:
        print("  SKIP: too few bars")
        return None

    clusters = find_clusters_relaxed(conn, sym, start, end, prices,
                                     max_clusters=max_clusters,
                                     threshold=threshold,
                                     min_vol=min_vol,
                                     price_band_pct=price_band)
    print(f"  Clusters: {len(clusters)}")
    if not clusters:
        return None

    trades = []
    pnl_total = 0
    wins = 0
    running_pnl = 0
    max_dd = 0

    for c in clusters:
        ctype = c["name"].split()[0]
        pnl = calc_pnl(sym, ctype, c["pc"], c["pf"])
        pnl_total += pnl
        running_pnl += pnl
        max_dd = min(max_dd, running_pnl)
        if pnl > 0:
            wins += 1
        trades.append({
            "name": c["name"],
            "type": ctype,
            "entry_time": c["start"],
            "exit_time": c["end"],
            "entry_price": c["pc"],
            "exit_price": c["pf"],
            "level": c["level"],
            "peak_vol": c["peak_vol"],
            "move_pips": c["move"],
            "pnl": round(pnl, 1),
        })

    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else gross_profit if gross_profit > 0 else 0
    avg_pnl = pnl_total / len(trades) if trades else 0

    return {
        "symbol": sym.upper(),
        "trades": len(trades),
        "wins": wins,
        "wr": wins / len(trades) * 100 if trades else 0,
        "pnl": round(pnl_total, 1),
        "pf": round(pf, 2),
        "dd": round(abs(max_dd), 1),
        "avg_pnl": round(avg_pnl, 1),
        "trades_list": trades,
        "clusters": clusters,
    }


def main():
    args = parse_args()
    symbols = [s.strip().lower() for s in args.sym.split(",")] if args.sym else SYMBOLS
    start, end = args.start, args.end

    conn = psycopg2.connect(**DB)
    print(f"Equity analysis (relaxed): {len(symbols)} symbols, {start} -> {end}")
    print(f"  params: threshold={args.threshold}x, min_vol={args.min_vol}lot, price_band=+/-{args.price_band}%")

    all_results = {}
    for sym in symbols:
        result = analyze_symbol(conn, sym, start, end,
                                args.max_clusters, args.threshold,
                                args.min_vol, args.price_band)
        if result:
            all_results[sym] = result
    conn.close()

    for sym, res in all_results.items():
        sf = OUTPUT_DIR / f"{sym}_equity_{start[:10]}_{end[:10]}.json"
        with open(sf, "w") as f:
            json.dump({
                "symbol": res["symbol"], "trades": res["trades"],
                "wins": res["wins"], "wr": res["wr"], "pnl": res["pnl"],
                "pf": res["pf"], "dd": res["dd"], "avg_pnl": res["avg_pnl"],
            }, f, indent=2)

    summary_file = OUTPUT_DIR / f"equity_{start[:10]}_{end[:10]}.html"
    if all_results:
        html = generate_dashboard(all_results, start, end)
        with open(summary_file, "w") as f:
            f.write(html)
        print(f"\\nDashboard: file://{summary_file}")

    total_trades = sum(r["trades"] for r in all_results.values())
    total_pnl = sum(r["pnl"] for r in all_results.values())
    print(f"\\n{'='*50}")
    print(f"TOTAL: {total_trades} trades, PnL {total_pnl:+.0f}p")
    for sym in symbols:
        if sym in all_results:
            r = all_results[sym]
            arrow = "\\U0001f7e2" if r["pnl"] >= 0 else "\\U0001f534"
            print(f"  {sym.upper():8s} {r['trades']:2d} trades  WR {r['wr']:5.1f}%  PnL {r['pnl']:+8.0f}p  PF {r['pf']:6.2f}  {arrow}")


if __name__ == "__main__":
    main()
