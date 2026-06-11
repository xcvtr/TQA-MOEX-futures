#!/usr/bin/env python3
"""
Walk-forward stability check for OI Divergence Limit signals.

72 param combos × 4 folds = 288 simulations.
Reports:
  - Top 10 combos by total return across folds
  - Combos profitable in ALL folds
  - Combos where DD ≤ 20% in all folds

Usage:
    python -m scripts.walkforward_oi_div_limit
"""

import sys, os, json, time
from itertools import product
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from scripts.bar_level_sim import BarLevelPortfolio


def _pre_group_fold(fold_signals):
    groups = {}
    for s in fold_signals:
        t = s['_time_dt']
        if t not in groups:
            groups[t] = []
        groups[t].append(s)
    sorted_times = sorted(groups.keys())
    return sorted_times, groups


def load_signals():
    path = '.signals_oi_div_limit.json'
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Run audit_strategies.py first.")
        sys.exit(1)
    with open(path) as f:
        raw = json.load(f)
    print(f"Loaded {len(raw)} signals from {path}")

    for s in raw:
        s['_time_dt'] = pd.Timestamp(s.get('time', ''))
    raw.sort(key=lambda x: x['_time_dt'])

    n = len(raw)
    n4 = n // 4
    folds = [raw[:n4], raw[n4:2*n4], raw[2*n4:3*n4], raw[3*n4:]]
    fold_groups = [_pre_group_fold(fs) for fs in folds]
    fold_sizes = [len(fs) for fs in folds]
    print(f"Fold sizes: {fold_sizes}")

    return fold_groups


def run_walkforward(fold_groups):
    mu_values = [0.05, 0.10, 0.15, 0.20]
    mc_values = [2, 3, 5, 8]
    tm_values = [0.10, 0.15, 0.20, 0.30]
    sl_values = [0.01, 0.02, 0.03, 0.05]

    total_combos = len(mu_values) * len(mc_values) * len(tm_values) * len(sl_values)
    print(f"Total combos: {total_combos} ({len(mu_values)} mu × {len(mc_values)} mc × {len(tm_values)} tm × {len(sl_values)} sl)")
    print(f"Total simulations: {total_combos * len(fold_groups)}")
    print()

    all_results = []
    combo_idx = 0

    for mu in mu_values:
        for mc in mc_values:
            for tm in tm_values:
                for sl in sl_values:
                    combo_idx += 1
                    if combo_idx % 10 == 0:
                        print(f"  Combo {combo_idx}/{total_combos}...", end='\r')

                    p = BarLevelPortfolio(
                        initial_capital=100000,
                        max_dd=mu,
                        max_concurrent=mc,
                        total_margin_limit=tm,
                        stop_loss_pct=sl,
                        use_score_sizing=True,
                        use_score_eviction=True,
                        atr_stop_mult=2.0,
                        use_score_decay=True,
                        max_hold_bars=40,
                        use_mtm=True,
                        use_trailing=False,
                        trailing_mult=3.0,
                        margin_usage=0.10,
                    )

                    fold_returns = []
                    fold_dds = []
                    fold_calmars = []
                    fold_trades = []

                    for sorted_times, time_groups in fold_groups:
                        r = p._run_grouped(sorted_times, time_groups)
                        fold_returns.append(r['total_return_pct'])
                        fold_dds.append(r['max_dd_pct'])
                        fold_calmars.append(r['calmar'])
                        fold_trades.append(len(r['trades']))

                    total_return = sum(fold_returns)
                    avg_dd = sum(fold_dds) / len(fold_dds)
                    max_dd = max(fold_dds)
                    avg_calmar = sum(fold_calmars) / len(fold_calmars)
                    profitable_all = all(r > 0 for r in fold_returns)
                    dd_ok_all = all(d <= 20 for d in fold_dds)

                    entry = {
                        'params': {'mu': mu, 'mc': mc, 'tm': tm, 'sl': sl},
                        'fold_returns': [round(r, 2) for r in fold_returns],
                        'fold_dds': [round(d, 2) for d in fold_dds],
                        'fold_calmars': [round(c, 4) for c in fold_calmars],
                        'fold_trades': fold_trades,
                        'total_return': round(total_return, 2),
                        'avg_dd': round(avg_dd, 2),
                        'max_dd': round(max_dd, 2),
                        'avg_calmar': round(avg_calmar, 4),
                        'profitable_all': profitable_all,
                        'dd_ok_all': dd_ok_all,
                    }
                    all_results.append(entry)

    print(f"\n  Done. {len(all_results)} combos evaluated.")
    return all_results


def print_results(all_results):
    # Top 10 by total return across all folds
    print(f"\n{'='*80}")
    print(f"  TOP 10 COMBOS BY TOTAL RETURN (sum of 4 folds)")
    print(f"{'='*80}")
    sorted_by_return = sorted(all_results, key=lambda x: x['total_return'], reverse=True)
    print(f" {'Rank':<5} {'Params':<30} {'TotRet':>8} {'AvgDD':>7} {'MaxDD':>7} {'AvgCalmar':>10} {'Folds':>6} | Fold Returns")
    print(f" {'-'*5} {'-'*30} {'-'*8} {'-'*7} {'-'*7} {'-'*10} {'-'*6} | {'-'*40}")
    for rank, e in enumerate(sorted_by_return[:15], 1):
        p = e['params']
        label = f"mu={p['mu']} mc={p['mc']} tm={p['tm']} sl={p['sl']}"
        print(f" {rank:<5} {label:<30} {e['total_return']:>8.2f} {e['avg_dd']:>7.2f} {e['max_dd']:>7.2f} {e['avg_calmar']:>10.4f} {'ALL' if e['profitable_all'] else '':>6} | {e['fold_returns']}")

    # Filter: profitable in ALL folds
    print(f"\n{'='*80}")
    print(f"  COMBOS PROFITABLE IN ALL 4 FOLDS")
    print(f"{'='*80}")
    profitable_all = [e for e in all_results if e['profitable_all']]
    profitable_dd_ok = [e for e in profitable_all if e['dd_ok_all']]
    profitable_all.sort(key=lambda x: x['total_return'], reverse=True)

    if profitable_all:
        print(f" {'Rank':<5} {'Params':<30} {'TotRet':>8} {'AvgDD':>7} {'MaxDD':>7} {'AvgCalmar':>10} | Fold Returns")
        print(f" {'-'*5} {'-'*30} {'-'*8} {'-'*7} {'-'*7} {'-'*10} | {'-'*40}")
        for rank, e in enumerate(profitable_all[:20], 1):
            p = e['params']
            label = f"mu={p['mu']} mc={p['mc']} tm={p['tm']} sl={p['sl']}"
            dd_ok = '✓' if e['dd_ok_all'] else '✗'
            print(f" {rank:<5} {label:<30} {e['total_return']:>8.2f} {e['avg_dd']:>7.2f} {e['max_dd']:>7.2f} {e['avg_calmar']:>10.4f} | {e['fold_returns']}  DD:{e['fold_dds']}  DD_ok:{dd_ok}")
    else:
        print(f"  NO combo is profitable in ALL 4 folds.")

    if profitable_dd_ok:
        print(f"\n{'='*80}")
        print(f"  COMBOS PROFITABLE IN ALL FOLDS + DD ≤ 20% IN ALL FOLDS")
        print(f"{'='*80}")
        for rank, e in enumerate(profitable_dd_ok[:10], 1):
            p = e['params']
            label = f"mu={p['mu']} mc={p['mc']} tm={p['tm']} sl={p['sl']}"
            print(f" {rank}. {label:<30} TotRet={e['total_return']:>6.2f}% AvgDD={e['avg_dd']:>5.2f}% AvgCalmar={e['avg_calmar']:.4f}")
    else:
        print(f"\n  NO combo meets ALL criteria (profitable in all folds + DD ≤ 20%).")

    return profitable_all, profitable_dd_ok


if __name__ == '__main__':
    t0 = time.time()
    fold_groups = load_signals()
    all_results = run_walkforward(fold_groups)
    print_results(all_results)
    print(f"\nTotal time: {time.time()-t0:.0f}s")
