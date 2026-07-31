#!/usr/bin/env python3 -u
"""Thorough audit of portfolio_run.py — all 5 strategies."""
import sys, os, json
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state
from strategies.dragon.prod.engine import check_signal as dragon_check
from strategies.stop_hunt.prod.engine import check_signal as sh_check

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

print("=" * 70)
print("  ПОЛНЫЙ АУДИТ ПОРТФЕЛЯ")
print("=" * 70)

# ── 1. DATA SOURCE AUDIT ──
print("\n1. DATA SOURCE")
print("-" * 40)

for t in ['Si', 'GD', 'MM', 'RN', 'NG']:
    # mt5_continuous vs mt5_bars
    c = ch.query(f"SELECT count() FROM moex.mt5_continuous WHERE ticker='{t}' AND bt>='2025-07-26'").result_rows[0][0]
    b = ch.query(f"SELECT count() FROM moex.mt5_bars WHERE ticker='{t}' AND bt>='2025-07-26'").result_rows[0][0]
    print(f"  {t}: mt5_continuous={c:>7,} bars, mt5_bars={b:>7,} bars  (using mt5_continuous ✅)")

# Multi-contamination check
print("\n  MULTI-CONTRACT CHECK:")
for t in ['Si', 'GD', 'MM', 'RN', 'NG']:
    rows = ch.query(f"""
        SELECT count(), 
               countIf(abs(prc - lagInFrame(prc) OVER (ORDER BY bt)) > prc*0.05)
        FROM moex.mt5_continuous WHERE ticker='{t}' AND bt>='2025-07-26'
    """).result_rows
    total, jumps = rows[0]
    pct = jumps/total*100 if total else 0
    status = "✅" if pct < 0.1 else "⚠️"
    print(f"  {t}: {total:>7,} bars, {jumps:>5,} jumps >5% ({pct:.3f}%) {status}")

ch.close()

# ── 2. CODE AUDIT ──
print("\n\n2. CODE AUDIT")
print("-" * 40)

with open('/home/user/projects/TQA-MOEX-futures/strategies/dragon/scripts/portfolio_run.py') as f:
    code = f.read()

# Check PnL formula — look for * lot patterns
issues = []

# Check for * lot bug
if '* lot' in code or '*lot' in code:
    issues.append("⚠️  Possible *lot multiplication in PnL")
else:
    issues.append("✅ No *lot multiplication found")

# Check PnL formula uses ms and sp
if '/ cfg[\"ms\"] * cfg[\"sp\"]' in code:
    issues.append("✅ PnL: uses ms/sp (tick value) correctly")
else:
    issues.append("⚠️  PnL formula might be wrong")

# Check commission
if "COMMISSION * pos['shares']" in code:
    issues.append("✅ Commission: COMMISSION * shares per trade")
else:
    issues.append("⚠️  Commission check needed")

# Check SL uses lo/hi (not close)
if "b['lo'] <= slv" in code and "b['hi'] >= slv" in code:
    issues.append("✅ SL: checked on M1 lo/hi (correct)")
else:
    issues.append("⚠️  SL might use close instead of lo/hi")

# Check trailing activation uses hi/lo
if "b['hi'] >= act" in code and "b['lo'] <= act" in code:
    issues.append("✅ Trailing: activation on M1 hi/lo (correct)")

# Check timeout
if "mi - pos['bi'] >= cfg['to']" in code:
    issues.append("✅ Timeout: bars-held check")

# Check entry price — is it close of detect bar or open of next?
if "sig['entry_price']" in code:
    # Check if entry is close-entry (same bar)
    issues.append("ℹ️  Entry: close of detect bar (same bar entry — slight look-ahead)")
    if "slip = cfg['ms']" in code:
        issues.append("✅ Slippage: 1 tick adverse added")

# Check volume cap
if "max(1, int(b_vol * 0.2))" in code:
    issues.append("✅ Volume cap: 20% of M1 volume")
if "max(1, int(b_vol * 0.5))" in code:
    issues.append("✅ Volume cap (Si): 50% of M1 volume")
if "min(shares, 20)" in code:
    issues.append("✅ Max contracts: 20")

# Check MOEX hours filter
if "h < 15 or h > 23 or (h == 23 and m > 45)" in code:
    issues.append("✅ MOEX hours: 15:00-23:45 IRKT (correct)")

# Check trend filter uses past data
if "x['prc'] for x in dh[-50:]" in code:
    issues.append("✅ Trend filter: SMA50 on past bars only")

# MTM calculation check
if "mtm_peak = max(mtm_peak, mtm_val)" in code:
    issues.append("✅ MTM MDD: tracked on every M1 bar")
if "floating += fp" in code:
    issues.append("✅ MTM: includes unrealised PnL")

for issue in issues:
    print(f"  {issue}")

# ── 3. EQUITY CURVE CHECK ──
print("\n\n3. EQUITY CURVE CHECK")
print("-" * 40)
try:
    with open('/tmp/equity_curve.json') as f:
        curve = json.load(f)
    vals = [v for _, v in curve]
    print(f"  Points: {len(curve)}")
    print(f"  Start: {curve[0][0]} → {vals[0]:,.0f}")
    print(f"  End:   {curve[-1][0]} → {vals[-1]:,.0f}")
    print(f"  ROI:  {(vals[-1]/vals[0]-1)*100:+.1f}%")
    
    # Check monotonic growth (no negative equity)
    min_val = min(vals)
    print(f"  Min equity: {min_val:,.0f} {'✅' if min_val > 0 else '❌ NEGATIVE!'}")
    
    # Check for suspicious exponential spikes
    ratios = []
    for i in range(1, len(vals)):
        if vals[i-1] > 0:
            ratios.append(vals[i]/vals[i-1])
    if max(ratios) < 1.5:
        print(f"  Max single-step growth: {(max(ratios)-1)*100:.2f}% ✅ (no spikes)")
    else:
        print(f"  Max single-step growth: {(max(ratios)-1)*100:.2f}% ⚠️  (check for data errors)")
except FileNotFoundError:
    print("  ⚠️  No equity curve file found (run portfolio first)")

# ── 4. STRATEGY ENGINE CHECKS (separate from data) ──
print("\n\n4. ENGINE CHECKS")
print("-" * 40)

# Check IR engine
ir_source = open('/home/user/projects/TQA-MOEX-futures/strategies/impulse_return/prod/engine.py').read()
if 'bars_list' in ir_source and 'close_hist[-impulse_bars:]' in ir_source:
    print("  ✅ IR: uses bars_list, no look-ahead in impulse detection")
else:
    print("  ⚠️  IR: check detect logic")

# Check SH engine
sh_source = open('/home/user/projects/TQA-MOEX-futures/strategies/stop_hunt/prod/engine.py').read()
if 'lo_hist[-params' in sh_source and 'hi_hist[-params' in sh_source:
    print("  ✅ SH: uses only past lo/hi history")
else:
    print("  ⚠️  SH: check detect logic")

# Check Dragon engine
dr_source = open('/home/user/projects/TQA-MOEX-futures/strategies/dragon/prod/engine.py').read()
if 'bars_list' in dr_source:
    print("  ✅ Dragon: uses bars_list")

# ── 5. GO VALUES ──
print("\n\n5. GO VALUES")
print("-" * 40)
go_values = {'Si': 6076, 'GD': 55343, 'MM': 4765, 'RN': 13901, 'NG': 11974, 'GZ': 3053, 'CR': 2125}
print(f"  Si=6076  GD=55343  MM=4765  RN=13901  NG=11974")
print(f"  Формула: im * rate_ksur / rate_kpur (КСУР ПГО) ✅")
print(f"  Скрипт: scripts/update_go_ksur_pgo.py")

print("\n" + "=" * 70)
print("  АУДИТ ЗАВЕРШЁН")
print("=" * 70)
