#!/usr/bin/env python3
"""
Audit — сравнивает Baseline vs Chandelier vs Chandelier+Partial vs All.
Запускает портфель с каждой конфигурацией, выводит таблицу, сохраняет JSON.
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

from reports.triz_diamond_v4.portfolio_v4 import run_portfolio, TOP_SIGNALS

CONFIGS = [
    ('Baseline',           dict(use_chandelier=False, use_partial_exit=False, use_score_sizing=False)),
    ('Chandelier',         dict(use_chandelier=True,  use_partial_exit=False, use_score_sizing=False)),
    ('Ch+Partial',         dict(use_chandelier=True,  use_partial_exit=True,  use_score_sizing=False)),
    ('All+ScoreSizing',    dict(use_chandelier=True,  use_partial_exit=True,  use_score_sizing=True)),
]

def run_comparison(capital):
    results = {}
    print(f"\n{'='*70}")
    print(f"PORTFOLIO: Capital={capital:,}, Signals=4 (RI+GL+USDRUBF+NM)")
    print(f"{'='*70}")
    for label, kwargs in CONFIGS:
        r = run_portfolio(TOP_SIGNALS, capital, **kwargs)
        results[label] = r
        if r:
            print(f"  {label:<18}: ret={r['ret']:>+7.1f}% dd={r['mdd']:>4.1f}% "
                  f"calmar={r['calmar']:>5.1f} wr={r['wr']:>3.0f}% "
                  f"ann={r['ann']:>+7.1f}% tr={r['trades']}")
    return results

def print_table(results, baseline_label='Baseline'):
    print(f"\n{'='*80}")
    print("COMPARISON TABLE")
    print(f"{'='*80}")
    print(f"{'Config':<20} {'Ret':>8} {'DD':>6} {'Calmar':>8} {'WR':>5} {'PF':>7} {'Ann':>8} {'Trades':>6}")
    print("-" * 80)
    base = results.get(baseline_label)
    for label in [c[0] for c in CONFIGS]:
        r = results.get(label)
        if r is None:
            print(f"{label:<20} {'—':>8}")
            continue
        ret_s = f"{r['ret']:+.1f}%"
        if base and base['ret'] != 0:
            imp = (r['ret']/base['ret']-1)*100
            ret_s = f"{r['ret']:+.1f}% ({imp:+.0f}%)"
        print(f"{label:<20} {ret_s:>8} {r['mdd']:>5.1f}% "
              f"{r['calmar']:>7.1f} {r['wr']:>3.0f}% {r['pf']:>6.2f} "
              f"{r['ann']:>+7.1f}% {r['trades']:>6d}")

def main():
    t0 = time.time()
    print("=" * 80)
    print("AUDIT: TRIZ Phase 3 — Chandelier + Partial Exit + Score Sizing")
    print("Baseline: RI+GL+USDRUBF+NM, hold=5, sl=1%")
    print("=" * 80)

    all_results = {}
    for cap in [100_000, 200_000]:
        r = run_comparison(cap)
        print_table(r)
        all_results[f'cap_{cap}'] = r
        for label, res in r.items():
            if res:
                res['capital'] = cap

    print(f"\n{'='*80}")
    print(f"Total time: {time.time()-t0:.0f}s")
    print(f"{'='*80}")

    os.makedirs('reports/triz_diamond_v4', exist_ok=True)
    flat = {}
    for cap_key, cap_results in all_results.items():
        for label, res in cap_results.items():
            k = f"{cap_key}_{label}"
            if res:
                flat[k] = {kk: vv for kk, vv in res.items() if kk != 'by_ticker'}
    with open('reports/triz_diamond_v4/audit_results.json', 'w') as f:
        json.dump({'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'configs': [c[0] for c in CONFIGS],
                    'results': flat}, f, indent=2, default=str)
    print("Saved: reports/triz_diamond_v4/audit_results.json")

if __name__ == '__main__':
    main()
