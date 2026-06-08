#!/usr/bin/env python3
"""Full scan v3 — all 7 strategies × M5 + H1 on ALL 64 tickers (including restored CR, MN, MY, RB, RL)."""
import sys, os, json, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trading_bot.new_strategies import (
    run_all_tickers, detect_vwap_signals, detect_mean_reversion_signals,
    detect_order_block_signals, detect_volume_surge_signals,
    detect_otc_signals, detect_retail_trap_signals, detect_oi_divergence_signals,
)

TICKERS = [
    "AF","AL","AU","BM","BR","CC","CE","CH","CNYRUBF","CR","DX","ED",
    "EURRUBF","Eu","FF","GAZPF","GD","GK","GL","GLDRUBF","GZ","HS",
    "HY","IB","IMOEXF","KC","LK","MC","ME","MG","MM","MN","MX","MY",
    "NA","NG","NM","NR","OJ","PD","PT","RB","RI","RL","RM","RN",
    "SBERF","SE","SF","Si","SN","SP","SR","SS","SV","TN","TT","UC",
    "USDRUBF","VB","VI","W4","X5","YD",
]
DAYS = 180

STRATEGIES = [
    ("VWAP Deviation",      detect_vwap_signals,           {}),
    ("Mean Reversion",      detect_mean_reversion_signals,  {}),
    ("Order Block",         detect_order_block_signals,     {}),
    ("Volume Surge",        detect_volume_surge_signals,    {}),
    ("OI Trend Confirmation", detect_otc_signals,           {}),
    ("Retail Trap",         detect_retail_trap_signals,     {}),
    ("OI Divergence",       detect_oi_divergence_signals,   {}),
]
TIMEFRAMES = ["m5", "h1"]

seen_tickers = set()
for sym in TICKERS:
    seen_tickers.add(sym)
print(f"Total tickers: {len(seen_tickers)}")
print()

for name, fn, cfg in STRATEGIES:
    for tf_name in TIMEFRAMES:
        cfg["timeframe"] = tf_name
        print("=" * 60)
        print(f"  {name} ({tf_name})")
        print("=" * 60)
        results = run_all_tickers(fn, TICKERS, days=DAYS, config=cfg)
        active = [(tk, r) for tk, r in results.items() if r.get("n", 0) > 0]
        active.sort(key=lambda x: -x[1]["wr"])
        print(f"  Tickers with signals: {len(active)}/{len(TICKERS)}")
        print(f"  {'Ticker':8s} {'n':>6s} {'WR':>6s} {'PF':>6s}")
        print(f"  {'-'*30}")
        for tk, r in active:
            print(f"  {tk:8s} n={r['n']:>5d}  WR={r['wr']:>5.1f}% PF={r['pf']:>5.2f}")
        if active:
            avg_wr = sum(r['wr'] for _, r in active) / len(active)
            avg_pf = sum(r['pf'] for _, r in active) / len(active)
            print(f"  {'-'*30}")
            print(f"  {'AVG':8s} {'':6s} WR={avg_wr:>5.1f}% PF={avg_pf:>5.2f}")
        print()
