#!/usr/bin/env python3 -u
"""Debug SH RN — compare mt5_bars vs mt5_continuous."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

for source in ['mt5_bars', 'mt5_continuous']:
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc,vol FROM moex.{source} WHERE ticker='RN' AND bt>='2026-01-16' ORDER BY bt").result_rows
    ch.close()
    
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    
    sig_count = 0
    for i in range(65, len(bars)):
        b = bars[i]
        lo_hist = [bars[j]['lo'] for j in range(i-60, i)]
        hi_hist = [bars[j]['hi'] for j in range(i-60, i)]
        bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'],
              'lo_hist': lo_hist, 'hi_hist': hi_hist}
        sig = sh_check(bd, 'RN', {'lookback': 60, 'retrace': 0.05})
        if sig:
            sig_count += 1
    
    # Also print some sample prices
    print(f'{source}: {len(bars)} bars, {sig_count} SH signals')
    if len(bars) > 10:
        print(f'  Prices: O={bars[10]["opn"]} H={bars[10]["hi"]} L={bars[10]["lo"]} C={bars[10]["prc"]}')
        print(f'  Prices: O={bars[100]["opn"]} H={bars[100]["hi"]} L={bars[100]["lo"]} C={bars[100]["prc"]}')
        print(f'  Last:   O={bars[-1]["opn"]} H={bars[-1]["hi"]} L={bars[-1]["lo"]} C={bars[-1]["prc"]}')
    print()
