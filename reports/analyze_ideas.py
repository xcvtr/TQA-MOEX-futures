#!/usr/bin/env python3
"""Quick analysis of grid results for idea generation."""
import json, sys, subprocess
from collections import defaultdict
import numpy as np

print("=" * 70)
print("ANALYSIS OF EXISTING GRID RESULTS")
print("=" * 70)

# Load daily grid
with open('/home/user/projects/TQA-MOEX/reports/triz_300pct/grid_daily.json') as f:
    daily = json.load(f)

# Filter for profitable, stable combos
valid = [r for r in daily if r['mean_ret'] > 0 and r['mean_dd'] < 15 and r['n_neg_folds'] <= 1 and r['n_trades'] >= 10]
print(f"\nTotal combos: {len(daily)}")
print(f"Valid (profitable, stable, >= 10 trades): {len(valid)}")

# Top 20 by score
valid_sorted = sorted(valid, key=lambda x: -x['score'])
print(f"\n{'='*70}")
print("TOP 20 COMBOS BY SCORE")
print(f"{'='*70}")
for i, r in enumerate(valid_sorted[:20]):
    print(f"{i+1:>2}. {r['ticker']:<8} {r['pattern']:<25} hold={r['hold']} sl={r['sl']:<5} ret={r['mean_ret']:>+7.2f}% dd={r['mean_dd']:>5.2f}% wr={r['wr']:>4.1f}% pf={r['pf']:>4.2f} trades={r['n_trades']:<4} score={r['score']:>6.2f}")

# Top 20 by return
print(f"\n{'='*70}")
print("TOP 20 COMBOS BY RETURN")
print(f"{'='*70}")
ret_sorted = sorted(valid, key=lambda x: -x['mean_ret'])
for i, r in enumerate(ret_sorted[:20]):
    print(f"{i+1:>2}. {r['ticker']:<8} {r['pattern']:<25} hold={r['hold']} sl={r['sl']:<5} ret={r['mean_ret']:>+7.2f}% dd={r['mean_dd']:>5.2f}% wr={r['wr']:>4.1f}% pf={r['pf']:>4.2f} trades={r['n_trades']:<4}")

# Quality combos: WR>55%, DD<5%, Ret>20%
print(f"\n{'='*70}")
print("QUALITY COMBOS (WR>55%, DD<5%, Ret>20%)")
print(f"{'='*70}")
quality = [r for r in valid if r['wr'] > 55 and r['mean_dd'] < 5 and r['mean_ret'] > 20]
quality.sort(key=lambda x: -x['mean_ret'])
print(f"Found {len(quality)} quality combos:")
for i, r in enumerate(quality[:20]):
    print(f"{i+1:>2}. {r['ticker']:<8} {r['pattern']:<25} hold={r['hold']} sl={r['sl']:<5} ret={r['mean_ret']:>+7.2f}% dd={r['mean_dd']:>5.2f}% wr={r['wr']:>4.1f}% pf={r['pf']:>4.2f}")

# Best combo per ticker
print(f"\n{'='*70}")
print("BEST COMBO PER TICKER")
print(f"{'='*70}")
by_ticker = {}
for r in valid:
    t = r['ticker']
    if t not in by_ticker or r['score'] > by_ticker[t]['score']:
        by_ticker[t] = r
for t, r in sorted(by_ticker.items(), key=lambda x: -x[1]['score'])[:25]:
    print(f"{t:<8} ret={r['mean_ret']:>+7.2f}% dd={r['mean_dd']:>5.2f}% wr={r['wr']:>4.1f}% score={r['score']:>6.2f} {r['pattern']:<25} hold={r['hold']} sl={r['sl']}")

# Pattern averages
print(f"\n{'='*70}")
print("PATTERN AVERAGES")
print(f"{'='*70}")
by_pattern = defaultdict(list)
for r in valid:
    by_pattern[r['pattern']].append(r)
for p, combos in sorted(by_pattern.items(), key=lambda x: -np.mean([c['mean_ret'] for c in x[1]])):
    rets = [c['mean_ret'] for c in combos]
    dds = [c['mean_dd'] for c in combos]
    print(f"{p:<25} avg_ret={np.mean(rets):>+7.2f}% median_ret={np.median(rets):>+7.2f}% max_ret={max(rets):>+7.2f}% avg_dd={np.mean(dds):>5.2f}% n={len(combos)}")

# Hold statistics
print(f"\n{'='*70}")
print("HOLD DISTRIBUTION")
print(f"{'='*70}")
for h in [1, 2, 3, 5]:
    h_combos = [r for r in valid_sorted[:200] if r['hold'] == h]
    if h_combos:
        rets = [r['mean_ret'] for r in h_combos]
        dds = [r['mean_dd'] for r in h_combos]
        print(f"hold={h}: avg_ret={np.mean(rets):>+7.2f}% median_ret={np.median(rets):>+7.2f}% avg_dd={np.mean(dds):>5.2f}% n={len(h_combos)}")

# SL statistics
print(f"\n{'='*70}")
print("SL DISTRIBUTION")
print(f"{'='*70}")
for sl in [0, 0.01, 0.02]:
    sl_combos = [r for r in valid_sorted[:200] if r['sl'] == sl]
    if sl_combos:
        rets = [r['mean_ret'] for r in sl_combos]
        dds = [r['mean_dd'] for r in sl_combos]
        print(f"sl={sl:<5}: avg_ret={np.mean(rets):>+7.2f}% median_ret={np.median(rets):>+7.2f}% avg_dd={np.mean(dds):>5.2f}% n={len(sl_combos)}")

# Top score combos with stable WR across folds
print(f"\n{'='*70}")
print("STABLE COMBOS (0 negative folds)")
print(f"{'='*70}")
stable = [r for r in valid_sorted if r['n_neg_folds'] == 0]
print(f"Total stable combos: {len(stable)}")
for i, r in enumerate(stable[:15]):
    print(f"{i+1:>2}. {r['ticker']:<8} {r['pattern']:<25} hold={r['hold']} sl={r['sl']:<5} ret={r['mean_ret']:>+7.2f}% dd={r['mean_dd']:>5.2f}% wr={r['wr']:>4.1f}%")

# Key insights
print(f"\n{'='*70}")
print("KEY INSIGHTS")
print(f"{'='*70}")

# What patterns dominate top 20?
top20_patterns = defaultdict(int)
for r in valid_sorted[:20]:
    top20_patterns[r['pattern']] += 1
print(f"\nTop 20 combo patterns:")
for p, c in sorted(top20_patterns.items(), key=lambda x: -x[1]):
    print(f"  {p}: {c}")

# What tickers dominate top 50?
top50_tickers = defaultdict(int)
for r in valid_sorted[:50]:
    top50_tickers[r['ticker']] += 1
print(f"\nTop 50 combo tickers:")
for t, c in sorted(top50_tickers.items(), key=lambda x: -x[1]):
    print(f"  {t}: {c}")

# What's the max concurrent signals a single ticker can support?
print(f"\nMulti-signal potential per ticker (top 50):")
ticker_combos = defaultdict(list)
for r in valid_sorted[:100]:
    ticker_combos[r['ticker']].append(r)
for t, combos in sorted(ticker_combos.items(), key=lambda x: -len(x[1])):
    if len(combos) >= 3:
        print(f"  {t}: {len(combos)} combos in top 100")

print("\n\nDone.")
