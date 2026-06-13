#!/usr/bin/env python3
"""Verify the look-ahead bug by tracing dv[i] calculation for a specific signal."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from datetime import datetime
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# Load GL data the same way as the script
ticker = 'GL'
d_rows = ch.query("""
    SELECT toDate(p.time) as d,
           argMax(p.open,p.time), argMax(p.high,p.time), argMax(p.low,p.time),
           argMax(p.close,p.time), argMax(p.volume,p.time),
           argMax(o.yur_buy,p.time), argMax(o.yur_sell,p.time),
           argMax(o.fiz_buy,p.time), argMax(o.fiz_sell,p.time),
           argMax(o.total_oi,p.time)
    FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
    WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
    GROUP BY d ORDER BY d
""", parameters={'t': ticker}).result_rows

dates = [str(r[0]) for r in d_rows]
vol = np.array([float(r[5]) for r in d_rows])

# Build dv same way as script
v_m = np.mean(vol) + 1
dv = np.diff(vol) / v_m

# Trace the first 3 signals from the backtest output
# Trade 1: signal=2024-03-27 (index?)
print("LOOK-AHEAD VERIFICATION")
print("="*100)

# Find the dates
for target_date, target_entry in [('2024-03-27', '2024-03-28'), 
                                   ('2024-04-19', '2024-04-22'),
                                   ('2024-04-23', '2024-04-24')]:
    try:
        sig_idx = dates.index(target_date)
        entry_idx = dates.index(target_entry)
        
        print(f"\nSignal: {target_date} (index {sig_idx})")
        print(f"Entry: {target_entry} (index {entry_idx})")
        
        # dv[sig_idx] = (vol[sig_idx+1] - vol[sig_idx]) / v_m
        if sig_idx < len(dv):
            print(f"  dv[{sig_idx}] = (vol[{sig_idx+1}] - vol[{sig_idx}]) / v_m")
            print(f"  vol[{sig_idx}] ({dates[sig_idx]})  = {vol[sig_idx]:.0f}")
            print(f"  vol[{sig_idx+1}] ({dates[sig_idx+1]}) = {vol[sig_idx+1]:.0f}")
            print(f"  dv[{sig_idx}] = ({vol[sig_idx+1]:.0f} - {vol[sig_idx]:.0f}) / {v_m:.0f} = {dv[sig_idx]:.2f}")
            
            print(f"  >>> dv[sig_idx] REQUIRES vol of {dates[sig_idx+1]}")
            print(f"  >>> Entry bar is {target_entry} (index {entry_idx})")
            print(f"  >>> dates[sig_idx+1] = {dates[sig_idx+1]}")
            if dates[sig_idx+1] == target_entry:
                print(f"  >>> CONFIRMED: dv[i] uses volume of the ENTRY day ({dates[sig_idx+1]})")
                print(f"  >>> This volume is NOT known at the signal close or entry open!")
                print(f"  >>> LOOK-AHEAD BUG CONFIRMED")
            else:
                print(f"  >>> dates[sig_idx+1] != entry date ({target_entry})")
                print(f"  >>> Actually dates[sig_idx+1] = {dates[sig_idx+1]} ≠ {target_entry}")
    except ValueError:
        print(f"  Could not find date")

# Now the CRITICAL BUG: GO vs contract value for leverage
print("\n" + "="*100)
print("CRITICAL LEVERAGE BUG ANALYSIS")
print("="*100)
print()
print("The backtest uses 'go = ep * cs' where:")
print("  ep = entry price ≈ 6873 to 13444 RUB")
print("  cs = 1")
print("  So 'go' ≈ 6873 to 13444")
print()
print("But 'go' from GO_MAP is set to 1352 RUB (the margin requirement).")
print("In the backtest function at line 160:")
print("  go = ep * cs    # This computes entry_price * contract_size")
print("  # For GL: go = 6873 * 1 = 6873")
print("  # This overrides the GO_MAP value of 1352!")
print()
print("So 'go' in the backtest = ep * cs = contract VALUE, not margin.")
print("The leverage check at line 171 reads:")
print("  max_by_go = int(eq * MAX_LEV / go) if go > 0 else 99")
print("  = int(eq * 5.0 / (ep * cs))")
print()
print(f"  For trade 1: eq=8696, ep=6872.9, cs=1")
print(f"  max_by_go = int(8696 * 5 / 6872.9) = int(6.33) = 6")
print()
print(f"  This means 6 contracts * 6872.9 = 41,237 buying power")
print(f"  equity * 5 = 43,478 buying power available")
print(f"  So 6 contracts at margin=6872.9 each = 41,237 of 43,478 buying power ✓")
print()
print("Wait, that means the total contract VALUE for 1 contract = ep ≈ 6873")
print("And max_by_go = int(8696 * 5 / 6873) = int(6.33) = 6")
print()
print("But the actual contract value is ep * cs ≈ 6873 * 1 = 6873")
print("And GO = 1352 RUB")
print("So what exactly is the margin?")
print("  Margin = GO = 1352 RUB (from GO_MAP)")
print("  Contract value = ep * cs ≈ 6873 RUB")
print()
print("The issue is: the code computes 'go = ep * cs' ON LINE 160,")
print("overriding the 'go_val' parameter entirely!")
print("For GL: cs=1, so go = ep * 1 = ep")
print("This is NOT the margin (1352), it's the contract value!")
print()
print("Let's check: what is 'ep * cs' really?")
print("  For futures, margin = GO = 1352, contract value ≈ price ≈ 80000")
print("  The code computes go = ep * cs = contract VALUE")
print("  Then uses it for max_by_go = int(eq * MAX_LEV / go)")
print("  = int(eq * 5 / contract_value)")
print()
print("This means max_by_go = int(8696 * 5 / 6873) = 6 for trade 1")
print("Which means you can hold 6 contracts with 5x leverage on contract VALUE.")
print()
print("BUT: For GL futures, cs=1 and price ≈ 6873... wait.")
print("The GL price in the data is ~6873 RUB?")
print("Then the contract value is ~6873 RUB, not 80000!")
print("That makes sense — GL on MOEX is mini-gold futures in RUB/gram!")
print()
print("So the contract value ~6873 RUB, margin (GO) = 1352 RUB")
print("Leverage for 1 contract = 6873/8696 = 0.79x — UNDER 1x, no problem!")
print("Max contracts by leverage = int(8696*5/6873) = 6")
print()
print("VERDICT: The leverage check is actually CORRECT.")
print("GL on MOEX trades at ~7000-13000 RUB (mini gold, not full ounce).")
print("Contract value ≈ 6873 RUB (small mini-contract)")
print("Margin (GO) = 1352 RUB")
print("So at entry capital 8696 RUB, 1 contract = 0.79x leverage — fine.")
print("No leverage bug — the contract is cheap enough.")
print()
print("BUT the GO_MAP value (1352) is NEVER USED in the backtest!")
print("Line 160: go = ep * cs OVERRIDES the go_val parameter.")
print("The go_val passed as parameter is only used nowhere in the function.")
print("Actually let me check... go_val is only used as a parameter, not referenced.")
print()
print("Line 128: def backtest_one(data, ticker, cs, go_val, pfunc, ...)")
print("Line 160: go = ep * cs   -- uses cs, not go_val!")
print("So go_val is a dead parameter!")
print()
print("But for GL with cs=1: go = ep * 1 = ep = contract VALUE.")
print("max_by_go = int(eq * MAX_LEV / go) = int(eq * 5 / ep)")
print("This limits contracts by: total_buying_power / contract_value")
print("Which is exactly how leverage SHOULD be computed.")
print("So the leverage check is mathematically correct for cs=1.")
print()
print("For other tickers with cs != 1: go = ep * cs")
print("e.g., Si with cs=1000: go = ep * 1000")
print("max_by_go = int(eq * 5 / (ep * 1000))")
print("The contract value = ep * 1000, buying power = eq * 5")
print("So it's still correct!")
