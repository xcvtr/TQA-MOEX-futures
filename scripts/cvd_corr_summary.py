#!/usr/bin/env python3
"""Print only the aggregate summary, line by line, small chunks."""
import json, sys
d = json.load(open('/tmp/cvd_corr_scan_ff53.json'))
results = d['results']

signal_types = ['CVD', 'CVD+OI_LVL', 'CVD+OI_FLW', 'CVD+YUR_LVL', 'CVD+YUR_FLW', 
                'CVD+FIZ', 'CVD+DISB', 'CVD+ANY', 'CVD+YUR_LVL_relax']

print("AGGR")
for sig in signal_types:
    with_sig = [r for r in results if f'{sig}_n' in r]
    if not with_sig:
        continue
    avg_n = sum(r[f'{sig}_n'] for r in with_sig) / len(with_sig)
    avg_wr = sum(r[f'{sig}_wr'] for r in with_sig) / len(with_sig)
    avg_net80 = sum(r[f'{sig}_net80'] for r in with_sig) / len(with_sig)
    print(f"{sig:<22} n={avg_n:>6.0f} WR={avg_wr:>5.1f}% NP80={avg_net80:>+6.2f} tkrs={len(with_sig)}")

print()
print("IMPROVED")
print("Ticker CVD CVD+OI_LVL CVD+OI_FLW CVD+YUR_LVL CVD+YUR_FLW CVD+DISB CVD+ANY")
for r in results:
    if 'CVD_net80' not in r:
        continue
    cvd = r['CVD_net80']
    oi_l = r.get('CVD+OI_LVL_net80', None)
    oi_f = r.get('CVD+OI_FLW_net80', None)
    yur_l = r.get('CVD+YUR_LVL_net80', None)
    yur_f = r.get('CVD+YUR_FLW_net80', None)
    disb = r.get('CVD+DISB_net80', None)
    anyv = r.get('CVD+ANY_net80', None)
    best = max([v for v in [oi_l, oi_f, yur_l, yur_f, disb, anyv] if v is not None], default=cvd)
    if best > cvd + 0.1:
        def f(v): return f'{v:+.2f}' if v is not None else '  N/A'
        print(f"{r['ticker']:<6} {cvd:+.2f} {f(oi_l)} {f(oi_f)} {f(yur_l)} {f(yur_f)} {f(disb)} {f(anyv)}")

print()
print("BEST_VARIANT")
print("Ticker CVD_NP80 BEST BEST_NP80")
for r in results:
    if 'CVD_net80' not in r: continue
    cvd = r['CVD_net80']
    best_sig = 'CVD'; best_v = cvd
    for sig in ['CVD+OI_LVL','CVD+OI_FLW','CVD+YUR_LVL','CVD+YUR_FLW','CVD+FIZ','CVD+DISB','CVD+ANY']:
        v = r.get(f'{sig}_net80')
        if v is not None and v > best_v + 0.05:
            best_v = v; best_sig = sig
    if best_sig != 'CVD':
        print(f"{r['ticker']:<6} {cvd:+.2f} {best_sig:<18} {best_v:+.2f}")
