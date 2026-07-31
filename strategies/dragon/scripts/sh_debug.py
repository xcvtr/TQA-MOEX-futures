#!/usr/bin/env python3 -u
"""Debug SH RN on mt5_continuous."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

# Load RN from mt5_continuous
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='RN' AND bt>='2026-01-16' ORDER BY bt").result_rows
ch.close()

bars = []
for r in rows:
    ts = r[0]; h, m = ts.hour, ts.minute
    if ts.weekday() >= 5: continue
    if h < 15 or h > 23 or (h == 23 and m > 45): continue
    bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})

print(f'RN mt5_continuous: {len(bars)} M1 bars', flush=True)

# Test SH detection
lb = 60
sig_count = 0
for i in range(lb+5, len(bars)):
    b = bars[i]
    lo_hist = [bars[j]['lo'] for j in range(i-lb, i)]
    hi_hist = [bars[j]['hi'] for j in range(i-lb, i)]
    bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'],
          'lo_hist': lo_hist, 'hi_hist': hi_hist}
    sig = sh_check(bd, 'RN', {'lookback': lb, 'retrace': 0.05})
    if sig:
        sig_count += 1
        if sig_count <= 5:
            print(f'  Signal {sig_count} @ bar {i}: {sig["direction"]} @ {sig["entry_price"]} ({sig["reason"]})', flush=True)

print(f'Total SH signals: {sig_count} in {len(bars)} bars', flush=True)
