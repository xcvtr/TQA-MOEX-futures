#!/home/user/venvs/tqa/main/bin/python
"""Test Cluster Regime Filter: block pairs when clusters run one-direction."""
import json, os, sys, warnings
from pathlib import Path
import pandas as pd
import numpy as np
import psycopg2
import importlib.util

warnings.filterwarnings('ignore')

DB = dict(host="10.0.0.64", port=5432, dbname="forex", user="postgres", password="postgres")
OUTDIR = Path("/home/user/.hermes/cache/screenshots/tqa/equity_cluster/2025")

SYMBOLS = ['audjpy','audusd','euraud','eurgbp','eurjpy','eurusd',
           'gbpjpy','gbpusd','nzdusd','usdcad','usdchf','usdjpy','xauusd']

# Load equity_results.json which has trades with entry/exit
with open(OUTDIR / "equity_results.json") as f:
    all_data = json.load(f)

# We need cluster-level data to know cluster direction
# The trades in equity_results.json have 'cluster' name field
# But they don't have type (long/short) directly
# Let's extract from the cluster name: 'LONG xxx' or 'SHORT xxx'

def get_cluster_type(trade):
    name = trade.get('cluster', '')
    name_upper = name.upper()
    if name_upper.startswith('LONG') or name_upper.startswith('BUY'):
        return 'long'
    elif name_upper.startswith('SHORT') or name_upper.startswith('SELL'):
        return 'short'
    # Try to infer from our_side
    side = str(trade.get('our_side', '')).lower()
    if side in ('long', 'short'):
        return side
    return None


# Test different regime filter parameters
# regime_window: how many consecutive same-type clusters trigger pause
# pause_length: how many subsequent clusters to block after trigger
print("CLUSTER REGIME FILTER")
print("=" * 80)
print("Tests: block pair when N consecutive clusters of same type appear,")
print("then pause for M subsequent clusters or until opposite type appears.")
print()

for regime_window in [2, 3, 4]:
    for pause_length in [2, 3, 5]:
        total_blocked = 0
        total_passed = 0
        total_pnl = 0
        base_total_pnl = 0
        
        print(f"Window={regime_window}, Pause={pause_length}:")
        
        for sym in SYMBOLS:
            trades = all_data.get(sym, {}).get('trades', [])
            if not trades:
                continue
            
            # Sort by entry time
            sorted_trades = sorted(trades, key=lambda t: str(t['entry']))
            
            base_pnls = [float(t['pnl_pips']) for t in sorted_trades]
            base_total_pnl += sum(base_pnls)
            
            # Apply regime filter
            passed = []
            regime_count = {'long': 0, 'short': 0}
            paused = 0
            last_type = None
            
            for t in sorted_trades:
                ctype = get_cluster_type(t)
                
                if paused > 0:
                    paused -= 1
                    if ctype != last_type:
                        paused = 0  # opposite type resets pause
                    continue
                
                if ctype is None:
                    passed.append(t)
                    continue
                
                # Check regime
                if ctype == last_type:
                    regime_count[ctype] += 1
                else:
                    regime_count = {'long': 0, 'short': 0, ctype: 1}
                
                if regime_count[ctype] >= regime_window:
                    # Trigger pause
                    paused = pause_length
                    last_type = ctype
                    continue
                
                passed.append(t)
                last_type = ctype
            
            pnls = [float(t['pnl_pips']) for t in passed]
            pnl = sum(pnls)
            total_pnl += pnl
            total_passed += len(passed)
            total_blocked += len(sorted_trades) - len(passed)
        
        delta = total_pnl - base_total_pnl
        em = '🟢' if delta > 0 else ('🔴' if delta < 0 else '⚪')
        total_trades_all = sum(len(all_data.get(s,{}).get('trades',[])) for s in SYMBOLS)
        print(f"  PnL={total_pnl:>+6.0f}p  Δ={delta:>+5.0f}{em}  "
              f"Passed={total_passed:>3d}/{total_trades_all}  Blocked={total_blocked:>3d}")
    
    print()


# Detailed for best config: window=3, pause=3
print("=" * 80)
print("DETAILED: Window=3, Pause=3")
print("=" * 80)
print(f"{'Symbol':8s} {'Base PnL':>8s} {'Base WR':>6s} | {'Filter PnL':>8s} {'WR':>6s} {'Blocked':>7s} {'ΔPnL':>8s}")
print("-" * 65)

total_base = 0
total_filter = 0

for sym in SYMBOLS:
    trades = all_data.get(sym, {}).get('trades', [])
    if not trades:
        continue
    
    sorted_trades = sorted(trades, key=lambda t: str(t['entry']))
    base_pnls = [float(t['pnl_pips']) for t in sorted_trades]
    base_pnl = sum(base_pnls)
    base_wr = sum(1 for t in sorted_trades if t['won']) / len(sorted_trades) * 100
    total_base += base_pnl
    
    passed = []
    regime_count = {'long': 0, 'short': 0}
    paused = 0
    last_type = None
    
    for t in sorted_trades:
        ctype = get_cluster_type(t)
        
        if paused > 0:
            paused -= 1
            if ctype != last_type:
                paused = 0
            continue
        
        if ctype is None:
            passed.append(t)
            continue
        
        if ctype == last_type:
            regime_count[ctype] += 1
        else:
            regime_count = {'long': 0, 'short': 0, ctype: 1}
        
        if regime_count[ctype] >= 3:
            paused = 3
            last_type = ctype
            continue
        
        passed.append(t)
        last_type = ctype
    
    pnls = [float(t['pnl_pips']) for t in passed]
    pnl = sum(pnls)
    wr = sum(1 for t in passed if t['won']) / len(passed) * 100 if passed else 0
    blk = len(sorted_trades) - len(passed)
    delta = pnl - base_pnl
    em = '🟢' if delta > 0 else ('🔴' if delta < 0 else '⚪')
    
    print(f"  {sym:8s} {base_pnl:>+7.0f}p  {base_wr:>5.1f}% | {pnl:>+7.0f}p  {wr:>5.1f}%  "
          f"{blk:>2d}/{len(sorted_trades):<2d}  {delta:>+5.0f}{em}")
    total_filter += pnl

print("-" * 65)
delta_t = total_filter - total_base
print(f"  TOTAL   {total_base:>+7.0f}p  | {total_filter:>+7.0f}p  Δ={delta_t:>+5.0f}p "
      f"{'🟢' if delta_t > 0 else '🔴'}")
