#!/home/user/venvs/tqa/main/bin/python
"""
TQA Stage 2 — Rolling Cluster Trading (optimized).
DOM data loaded ONCE per symbol, then filtered by window in pandas.
"""

import argparse, json, os, sys, warnings
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

import psycopg2
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

DB = dict(host="10.0.0.60", port=5432, dbname="forex", user="postgres", password="postgres")
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
    p.add_argument("--window", type=int, default=30)
    p.add_argument("--slide", type=int, default=14)
    p.add_argument("--top-n", type=int, default=6)
    return p.parse_args()


def get_price_at(pdf, ts):
    if pdf.empty:
        return None
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        target = pd.Timestamp(ts).tz_convert("UTC")
    else:
        target = pd.Timestamp(ts, tz="UTC")
    idx = pdf["t"].sub(target).abs().idxmin()
    return float(pdf.loc[idx, "price"])


def calc_pnl(sym, typ, entry, exit):
    mult = 100 if (sym in JPY_PAIRS or sym in XAU_PAIRS) else 10000
    if typ == "SHORT":
        return (entry - exit) * mult
    return (exit - entry) * mult


def load_all(conn, sym, start, end):
    """Load ALL DOM + price data for a symbol (one query)."""
    if sym in JPY_PAIRS:
        dec = 1
    elif sym in XAU_PAIRS:
        dec = 0
    else:
        dec = 2

    price = pd.read_sql(
        f"SELECT time AT TIME ZONE 'UTC' as t, price FROM {sym}_data "
        f"WHERE time >= '{start}' AND time < '{end}' AND price > 0 ORDER BY time", conn
    )
    price["t"] = pd.to_datetime(price["t"], utc=True)
    if len(price) < 50:
        return None, None, None, dec

    p_med = price["price"].median()
    p_lo, p_hi = p_med * 0.85, p_med * 1.15

    dom = pd.read_sql(
        f"SELECT time AT TIME ZONE 'UTC' as t, "
        f"ROUND(price::numeric, {dec}) as level, positions "
        f"FROM {sym}_dom "
        f"WHERE time >= '{start}' AND time < '{end}' "
        f"AND positions IS NOT NULL AND positions != 0 "
        f"AND price >= {p_lo} AND price <= {p_hi}", conn
    )
    dom["t"] = pd.to_datetime(dom["t"], utc=True)

    if sym in JPY_PAIRS:
        prox = 1.0
    elif sym in XAU_PAIRS:
        prox = 5.0
    else:
        prox = 0.004

    return dom, price, prox, dec


def top_clusters_in_window(dom_slice, price_slice, prox, dec, top_n=6):
    """Find top-N cluster levels in a DOM slice."""
    if dom_slice.empty or price_slice.empty or len(price_slice) < 10:
        return []

    st = dom_slice.groupby("level").agg(
        total_pos=("positions", lambda x: x.abs().sum()),
        net_pos=("positions", "sum"),
        cnt=("positions", "count"),
    ).reset_index()
    st = st[st["cnt"] >= 3].copy()
    if st.empty:
        return []

    zones = []
    for _, r in st.sort_values("total_pos", ascending=False).iterrows():
        cl = r["level"]
        found = False
        for z in zones:
            if abs(cl - z["c"]) < prox * 1.5:
                ov = z["v"]
                z["c"] = (z["c"] * ov + cl * r["total_pos"]) / (ov + r["total_pos"])
                z["v"] += r["total_pos"]
                z["n"] += r["net_pos"]
                found = True
                break
        if not found:
            zones.append({"c": cl, "v": r["total_pos"], "n": r["net_pos"]})

    if not zones:
        return []
    zones.sort(key=lambda x: x["v"], reverse=True)

    longs = [z for z in zones if z["n"] > 0]
    shorts = [z for z in zones if z["n"] <= 0]

    sel, li, si = [], 0, 0
    while len(sel) < top_n:
        if li < len(longs):
            sel.append(longs[li]); li += 1
        if len(sel) >= top_n:
            break
        if si < len(shorts):
            sel.append(shorts[si]); si += 1
        if li >= len(longs) and si >= len(shorts):
            break

    return [{
        "level": round(z["c"], dec),
        "type": "LONG" if z["n"] > 0 else "SHORT",
        "total_pos": round(z["v"], 1),
    } for z in sel]


def analyze_symbol(conn, sym, start, end, wd=30, sd=14, top_n=6):
    dom, price, prox, dec = load_all(conn, sym, start, end)
    if dom is None:
        return []

    sdt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    edt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    trades = []
    streak, absent = defaultdict(int), defaultdict(int)
    win = 0

    ws = sdt
    while ws < edt:
        we = ws + timedelta(days=wd)
        dm = (dom["t"] >= ws) & (dom["t"] < we)
        pm = (price["t"] >= ws) & (price["t"] < we)

        clusters = top_clusters_in_window(dom[dm], price[pm], prox, dec, top_n)
        cur = {c["level"] for c in clusters}
        wstr = ws.strftime("%Y-%m-%d")

        for c in clusters:
            streak[c["level"]] += 1
            absent[c["level"]] = 0
        for lv in list(streak.keys()):
            if lv not in cur:
                absent[lv] += 1

        # ENTER
        for c in clusters:
            if streak[c["level"]] == 1:
                ep = get_price_at(price[pm], ws)
                if ep is not None:
                    trades.append({
                        "level": c["level"], "type": c["type"],
                        "entry_time": wstr, "entry_price": round(ep, 5),
                        "exit_time": None, "exit_price": None,
                        "pnl": None, "status": "open",
                        "window": win,
                    })

        # EXIT (absent >= 2 windows)
        for t in trades:
            if t["status"] != "open":
                continue
            if absent[t["level"]] >= 2:
                ep = get_price_at(price[pm], ws)
                if ep is not None:
                    t["exit_time"] = wstr
                    t["exit_price"] = round(ep, 5)
                    t["pnl"] = round(calc_pnl(sym, t["type"], t["entry_price"], ep), 1)
                    t["status"] = "closed"

        win += 1
        ws += timedelta(days=sd)

    # Close remaining at end
    if trades and not price.empty:
        fp = float(price["price"].iloc[-1])
        for t in trades:
            if t["status"] == "open":
                t["exit_time"] = end
                t["exit_price"] = round(fp, 5)
                t["pnl"] = round(calc_pnl(sym, t["type"], t["entry_price"], fp), 1)
                t["status"] = "closed"

    return trades


def main():
    args = parse_args()
    symbols = [s.strip().lower() for s in args.sym.split(",")] if args.sym else SYMBOLS
    start, end = args.start, args.end

    conn = psycopg2.connect(**DB)
    print(f"Stage 2 — Rolling cluster (optimized)")
    print(f"  {len(symbols)} symbols, {start} -> {end}")
    print(f"  window={args.window}d, slide={args.slide}d, top-N={args.top_n}")

    results = {}
    for sym in symbols:
        trades = analyze_symbol(conn, sym, start, end, args.window, args.slide, args.top_n)
        closed = [t for t in trades if t["status"] == "closed" and t["pnl"] is not None]
        print(f"  {sym.upper():8s}: {len(closed)} trades")

        if closed:
            pnl = sum(t["pnl"] for t in closed)
            wins = sum(1 for t in closed if t["pnl"] > 0)
            wr = wins / len(closed) * 100
            gp = sum(t["pnl"] for t in closed if t["pnl"] > 0)
            gl = abs(sum(t["pnl"] for t in closed if t["pnl"] < 0))
            pf = gp / gl if gl > 0 else 0
            results[sym] = {
                "trades": len(closed), "wins": wins, "wr": round(wr, 1),
                "pnl": round(pnl, 1), "pf": round(pf, 2),
                "trades_list": closed,
            }

    conn.close()

    tp = sum(r["pnl"] for r in results.values())
    tt = sum(r["trades"] for r in results.values())
    tw = sum(r["wins"] for r in results.values())
    twr = tw / tt * 100 if tt > 0 else 0

    print(f"\n{'='*60}")
    print(f"TOTAL: {tt} trades, WR {twr:.1f}%, PnL {tp:+.0f}p")
    for sym in symbols:
        if sym in results:
            r = results[sym]
            arrow = "\\U0001f7e2" if r["pnl"] >= 0 else "\\U0001f534"
            print(f"  {sym.upper():8s} {r['trades']:3d} trades  WR {r['wr']:5.1f}%  PnL {r['pnl']:+8.0f}p  PF {r['pf']:6.2f}  {arrow}")

    sf = OUTPUT_DIR / f"stage2_{start[:10]}_{end[:10]}.json"
    # Clean trades_list for JSON serialization
    def clean_trade(t):
        return {k: v for k, v in t.items() if k != "status"}
    
    summary = {
        "params": {"window": args.window, "slide": args.slide, "top_n": args.top_n},
        "total": {"trades": tt, "wins": tw, "wr": twr, "pnl": tp},
        "symbols": {s: {"trades": r["trades"], "wins": r["wins"], "wr": r["wr"],
                        "pnl": r["pnl"], "pf": r["pf"],
                        "trades_list": [clean_trade(t) for t in r.get("trades_list", [])]}
                    for s, r in results.items()},
    }
    with open(sf, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nSaved: {sf} ({os.path.getsize(sf)} bytes)")


if __name__ == "__main__":
    main()
