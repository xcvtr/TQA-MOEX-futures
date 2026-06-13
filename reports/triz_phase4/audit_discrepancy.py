#!/usr/bin/env python3
"""Verify the discrepancy between +755% (grid result) and +177% (my audit)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

# The grid shows 68 trades, ret=+755%, net_pnl=65691, capital=8696
# My audit shows 68 trades, ret=+177%, net_pnl=15444

# Capital: 8696 (200K/23)
# net_pnl: I got 15444 vs grid's 65691
# ret: I got +177% vs grid's +755%

# Let's check: is my capital the same? CAPITAL/N = 200000/23 = 8695.65
# In the grid: N = len(ticker_data) which is number of tickers with data

# Let me check how many tickers actually have data
print("Checking actual N used in the grid run...")

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

TICKERS = ['CC', 'NM', 'PD', 'SV', 'VB', 'GD', 'SR', 'LK', 'PT', 'Si', 'Eu', 'CNYRUBF', 'CR', 'NG', 'MX', 'AL', 'RN']

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

count_ok = 0
for ticker in TICKERS:
    d_rows = ch.query("""
        SELECT toDate(p.time) as d
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    ok = len(d_rows) >= 60
    if ok:
        count_ok += 1
    print(f"  {ticker}: {len(d_rows)} bars {'OK' if ok else 'SHORT'}")

print(f"\nTotal OK: {count_ok}")
print(f"Capital per ticker: {200000/count_ok:.0f}")

# Now check if the discrepancy comes from how the grid re-runs the portfolio
# In the grid, the 'portfolio' re-run uses len(pf_signals) which is 6, not N=23
# But the individual results were computed with N=23

# Wait - the grid result shows ret=+755%, net_pnl=65691 at capital=8696
# But my audit with EXACT same params shows ret=+177%, net_pnl=15444
# This is a 4x difference. Let me check what could cause this.

# Difference 1: The grid calls backtest_one with different sl_pct
# Looking at line 263-294: 
# For the chandelier variants, it iterates sl_pct in [0.005,0.01,0.02]
# But the result shows sl=0 for chandelier mode
# Line 280: r2.update(dict(ticker=ticker,pattern=pname,hold=hold,sl=0,...))
# But line 276 passes sl_pct as argument!

print("\n\nCHECKING THE GRID CODE CAREFULLY...")
print("="*60)
print("Line 263-294 of megagrid.py:")
print()
print("for hold in [1,2,3,5,8,13,21]:")
print("    for sl_pct in [0.005,0.01,0.02]:")
print("        for dv_thr in [0,1.0,2.0]:")
print("            # Plain vanilla")
print("            r=backtest_one(..., hold, sl_pct, dv_thr, cap_per_ticker=cap_pt)")
print("            ")
print("            # Chandelier variants")
print("            for am in [2,3,5]:")
print("                r2=backtest_one(..., hold, sl_pct, dv_thr,")
print("                                 use_chandelier=True, atr_mult=am,")
print("                                 cap_per_ticker=cap_pt)")
print("                r2.update(..., sl=0, ...)   # Stored sl=0 in JSON")
print()
print("!!! BUG: The chandelier backtest receives sl_pct=0.005 (or 0.01, 0.02)")
print("!!! But the result is STORED with sl=0!")
print("!!! sl_pct is NOT used inside the chandelier branch, so it doesn't matter")
print("!!! for the chandelier logic. BUT it IS used for the non-chandelier branch.")
print()
print("Wait, sl_pct IS used in the chandelier path:")
print("Line 165: if sl_pct>0:   base_nc=risk_amount/(go*sl_pct)")
print("Line 168: else:           base_nc=risk_amount/go*5")
print()
print("With chandelier=True and sl_pct=0.005:")
print("  sl_pct > 0, so base_nc = risk_amount / (go * sl_pct)")
print("  = 173.9 / (6873 * 0.005) = 173.9 / 34.4 = 5.06")
print("  base_nc = max(1, int(5.06)) = 5")
print()
print("With sl_pct=0 (my audit):")
print("  base_nc = max(1, int(rsk/go*5))")
print("  = max(1, int(173.9/6873*5)) = max(1, int(0.126)) = 1")
print()
print("!!! THAT'S THE BUG!")
print("!!! When sl_pct=0.005, nc=5  → leverage = 5*6873/8696 = 3.95x")
print("!!! When sl_pct=0 (fixed), nc=1 → leverage = 6873/8696 = 0.79x")
print()
print("The grid iterates sl_pct in [0.005, 0.01, 0.02] for chandelier mode,")
print("using sl_pct for SIZING even in chandelier mode!")
print("sl_pct controls the stop-loss percentage for SIZING purposes:")
print("  base_nc = risk_amount / (go * sl_pct)")
print("With smaller sl_pct, each contract has a tighter stop")
print("So you can buy MORE contracts for the same risk amount!")
print()
print("sl_pct=0.005 → base_nc=5 contracts")
print("sl_pct=0.01  → base_nc=3 contracts")  
print("sl_pct=0.02  → base_nc=2 contracts")
print()
print("But the stored result shows sl=0, hiding this parameter!")
print("This is a STORAGE BUG — the sl_pct used for sizing is not saved.")
print()
print("When I re-run with the EXACT parameters, I need to pass sl_pct=0.005")
print("to match the grid result, even though it shows sl=0 in JSON!")
print()
print("The top result has hold=13, chandelier=True, atr_mult=2, sl=0, dv_thr=0")
print("This is the FIRST iteration of the loop: sl_pct=0.005, dv_thr=0")
print("So sl_pct=0.005 was used for sizing!")
print()
print(f"Let's verify: with sl_pct=0.005:")
risk_amount = 8695.65 * 0.02  # 173.9
go = 6872.9  # first trade
base_nc = max(1, int(risk_amount / (go * 0.005)))
print(f"  base_nc = max(1, int({risk_amount:.1f} / ({go:.1f} * 0.005)))")
print(f"          = max(1, int({risk_amount:.1f} / {go*0.005:.1f}))")
print(f"          = max(1, int({risk_amount/(go*0.005):.1f}))")
print(f"          = max(1, {int(risk_amount/(go*0.005))}) = {base_nc}")
print(f"  With nc={min(base_nc, 5, int(8695.65*5/6872.9))}")
