#!/usr/bin/env python3
"""Generate correlation analysis report from cvd_corr_scan results."""
import json, os, uuid

d = json.load(open('/tmp/cvd_corr_scan_ff53.json'))
results = d['results']

signal_types = ['CVD', 'CVD+OI_LVL', 'CVD+OI_FLW', 'CVD+YUR_LVL', 'CVD+YUR_FLW', 
                'CVD+FIZ', 'CVD+DISB', 'CVD+ANY', 'CVD+YUR_LVL_relax']

print("=" * 100)
print("CORRELATION SCAN: CVD signal variants vs available data")
print("=" * 100)

# 1. Aggregate summary per signal type
print(f"\n--- Aggregate across all {len(results)} tickers ---")
print(f"{'Signal':<22} {'Avg_n':>8} {'Avg_WR%':>8} {'Avg_Net80':>10} {'Tickers':>8}")
print('-' * 60)

for sig in signal_types:
    with_sig = [r for r in results if f'{sig}_n' in r]
    if not with_sig:
        continue
    avg_n = sum(r[f'{sig}_n'] for r in with_sig) / len(with_sig)
    avg_wr = sum(r[f'{sig}_wr'] for r in with_sig) / len(with_sig)
    avg_net80 = sum(r[f'{sig}_net80'] for r in with_sig) / len(with_sig)
    print(f"{sig:<22} {avg_n:>8.0f} {avg_wr:>8.1f} {avg_net80:>+10.2f} {len(with_sig):>8}")

# 2. Show tickers where CVD+ANY improves over CVD alone
print(f"\n--- Tickers where CVD+ANY > CVD (on net80) ---")
improved = []
for r in results:
    if 'CVD_net80' not in r or 'CVD+ANY_net80' not in r:
        continue
    cvd_np = r['CVD_net80']
    any_np = r['CVD+ANY_net80']
    if any_np > cvd_np:
        improved.append((r['ticker'], cvd_np, any_np, any_np - cvd_np))
improved.sort(key=lambda x: x[3], reverse=True)
print(f"Improved: {len(improved)} / {len([r for r in results if 'CVD_net80' in r])}")
print(f"{'Ticker':<8} {'CVD':>8} {'CVD+ANY':>9} {'Δ':>8}")
for t, c, a, d in improved[:15]:
    print(f"{t:<8} {c:>+8.2f} {a:>+9.2f} {d:>+8.2f}")

# 3. Best signal variant per ticker
print(f"\n--- Best signal variant per ticker ---")
print(f"{'Ticker':<8} {'BestSig':<18} {'NetP80':>8} {'WR%':>7} {'n':>6} {'CVD_n':>6}")
for r in results:
    best_sig = 'CVD'
    best_np = r.get('CVD_net80', -999)
    best_wr = r.get('CVD_wr', 0)
    best_n = r.get('CVD_n', 0)
    for sig in signal_types[1:]:
        np_val = r.get(f'{sig}_net80', -999)
        if np_val > best_np:
            best_sig = sig
            best_np = np_val
            best_wr = r.get(f'{sig}_wr', 0)
            best_n = r.get(f'{sig}_n', 0)
    cvd_np = r.get('CVD_net80', 0)
    if best_sig != 'CVD' and best_np > cvd_np + 0.1:
        print(f"{r['ticker']:<8} {best_sig:<18} {best_np:>+8.2f} {best_wr:>7.1f} {best_n:>6} {r.get('CVD_n',0):>6}")

# 4. Cross-ticker correlation: CVD performance vs ticker characteristics
print(f"\n--- Cross-ticker correlations: CVD_net80 vs data characteristics ---")
# Find tickers that have all metrics
full = [r for r in results if 'CVD_net80' in r]
print(f"Tickers with full data: {len(full)}")

# Group by has_yur
has_yur = [r for r in full if r['has_yur']]
no_yur = [r for r in full if not r['has_yur']]
if has_yur:
    avg_yur = sum(r['CVD_net80'] for r in has_yur) / len(has_yur)
    print(f"  Has YUR data ({len(has_yur)}): Avg CVD_net80 = {avg_yur:+.2f}")
if no_yur:
    avg_no_yur = sum(r['CVD_net80'] for r in no_yur) / len(no_yur)
    print(f"  No YUR data ({len(no_yur)}): Avg CVD_net80 = {avg_no_yur:+.2f}")

# Group by has_hi2
has_hi2 = [r for r in full if r['has_hi2']]
no_hi2 = [r for r in full if not r['has_hi2']]
if has_hi2:
    avg_hi2 = sum(r['CVD_net80'] for r in has_hi2) / len(has_hi2)
    print(f"  Has HHI data ({len(has_hi2)}): Avg CVD_net80 = {avg_hi2:+.2f}")
if no_hi2:
    avg_no_hi2 = sum(r['CVD_net80'] for r in no_hi2) / len(no_hi2)
    print(f"  No HHI data ({len(no_hi2)}): Avg CVD_net80 = {avg_no_hi2:+.2f}")

# Correlation with mean_oi
oi_vals = [(r['mean_oi'], r['CVD_net80']) for r in full if 'mean_oi' in r and r['mean_oi'] > 0]
if len(oi_vals) > 5:
    # Sort by OI, split into terciles
    oi_vals.sort(key=lambda x: x[0])
    n3 = len(oi_vals) // 3
    low_oi = sum(v[1] for v in oi_vals[:n3]) / n3
    mid_oi = sum(v[1] for v in oi_vals[n3:2*n3]) / n3
    high_oi = sum(v[1] for v in oi_vals[2*n3:]) / (len(oi_vals) - 2*n3)
    print(f"  CVD_net80 by OI tercile: Low={low_oi:+.2f} Mid={mid_oi:+.2f} High={high_oi:+.2f}")

# Full individual ticker breakdown
print(f"\n--- Full breakdown (all 57 tickers) ---")
print(f"{'Ticker':<6} {'CVD_n':>6} {'CVD_WR':>7} {'CVD_NP':>7} {'+OI_L':>7} {'+OI_F':>7} {'+YUR_L':>7} {'+YUR_F':>7} {'+FIZ':>7} {'+DISB':>7} {'+ANY':>7} {'Best':<18}")
print('-' * 110)
for r in results:
    if 'CVD_net80' not in r: continue
    cvd_np = r['CVD_net80']
    # Get all variant net80 values
    vals = {}
    for sig in signal_types:
        vals[sig] = r.get(f'{sig}_net80', None)
    
    # Find best non-CVD variant
    best_sig = 'CVD'; best_np = cvd_np
    for sig in signal_types[1:]:
        if vals[sig] is not None and vals[sig] > best_np + 0.05:
            best_np = vals[sig]; best_sig = sig
    
    def fmt(v):
        return f'{v:>+7.2f}' if v is not None else '     -'
    
    marker = '★' if best_sig != 'CVD' else ' '
    print(f"{marker}{r['ticker']:<5} {r.get('CVD_n',0):>6} {r.get('CVD_wr',0):>7.1f} {cvd_np:>+7.2f} "
          f"{fmt(vals['CVD+OI_LVL'])} {fmt(vals['CVD+OI_FLW'])} {fmt(vals.get('CVD+YUR_LVL'))} "
          f"{fmt(vals.get('CVD+YUR_FLW'))} {fmt(vals.get('CVD+FIZ'))} "
          f"{fmt(vals.get('CVD+DISB'))} {fmt(vals.get('CVD+ANY'))} {best_sig:<18}")

# Save report
p = f'/tmp/cvd_corr_report_{uuid.uuid4().hex[:4]}.txt'
with open(p, 'w') as f:
    pass  # just mark it
print(f"\nReport generated. Data in: /tmp/cvd_corr_scan_ff53.json")
