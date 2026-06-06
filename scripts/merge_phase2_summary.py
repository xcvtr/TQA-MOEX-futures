#!/usr/bin/env python3
"""Merge existing per-ticker results into a complete SUMMARY.md.
Reads all reports/phase2/<TICKER>/details.csv files and builds the leaderboard."""

import os, csv

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports', 'phase2')

KEEP_TICKERS = ['GL','AF','CC','CE','DX','HS','HY','MC','MG','NG','NM','NR',
                'OJ','PD','SE','SF','SN','SP','SS','TN','TT','W4','YD','AL']
MAYBE_TICKERS = ['BM','GK','IB','KC','ME','MM','PT','RN','SV','VB']
ALL_TICKERS = KEEP_TICKERS + MAYBE_TICKERS

def classify(r):
    if r['n_signals'] == 0:
        return '❌ NO SIGNALS'
    wr = r['win_rate']
    pf = r['profit_factor']
    n  = r['n_signals']
    if wr >= 60 and pf >= 1.5 and n >= 30:
        return '✅ KEEP'
    if wr >= 55 and pf >= 1.3 and n >= 20:
        return '🟡 WATCH'
    if wr >= 50 and pf >= 1.0 and n >= 10:
        return '🔵 POSSIBLE'
    return '⚪ NOISE'

def load_results(symbol):
    csv_path = os.path.join(REPORTS_DIR, symbol, 'details.csv')
    if not os.path.exists(csv_path):
        return []
    results = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row['n_signals'] = int(row['n_signals'])
            row['win_rate'] = float(row['win_rate'])
            row['profit_factor'] = float(row['profit_factor'])
            row['avg_return'] = float(row['avg_return'])
            row['max_drawdown'] = float(row['max_drawdown'])
            row['gains'] = float(row.get('gains', 0))
            row['losses'] = float(row.get('losses', 0))
            row['vol_z_thr'] = float(row['vol_z_thr'])
            row['div_z_thr'] = float(row['div_z_thr'])
            row['horizon_label'] = row.get('horizon_label', f"{row['horizon']}m")
            results.append(row)
    return results

def find_best(results, direction):
    """Find best config for a direction win-rate first, then PF."""
    candidates = [r for r in results if r['direction'] == direction and r['n_signals'] >= 5]
    if not candidates:
        return None
    best = candidates[0]
    for r in candidates[1:]:
        if (r['win_rate'] > best['win_rate'] or
            (r['win_rate'] == best['win_rate'] and r['profit_factor'] > best['profit_factor'])):
            best = r
    return best

def main():
    leaders = []
    for sym in sorted(ALL_TICKERS):
        results = load_results(sym)
        for dir_name in ['LONG', 'SHORT']:
            best = find_best(results, dir_name)
            if best:
                leaders.append(best)
            else:
                leaders.append({
                    'symbol': sym, 'direction': dir_name, 'n_signals': 0,
                    'win_rate': 0.0, 'profit_factor': 0.0, 'avg_return': 0.0,
                    'max_drawdown': 0.0, 'gains': 0.0, 'losses': 0.0,
                    'div_z_thr': 0, 'vol_z_thr': 0, 'horizon_label': '-'
                })

    # Sort by win rate descending
    leaders.sort(key=lambda x: -x['win_rate'])

    lines = []
    lines.append("# Phase 2 — Volume Surge + FIZ/YUR Divergence Analysis")
    lines.append("")
    lines.append("**Combined results from all tickers**")
    lines.append("")
    lines.append("## Leaderboard (all tickers)")
    lines.append("")
    lines.append("| Тикер | Напр. | Сигн | WR% | PF | Avg% | DD% | Класс |")
    lines.append("|-------|-------|------|-----|----|------|-----|-------|")
    for r in leaders:
        cls = classify(r)
        lines.append(f"| {r['symbol']:6s} | {r['direction']:5s} | {r['n_signals']:4d} | "
                     f"{r['win_rate']:4.1f} | {r['profit_factor']:3.2f} | "
                     f"{r['avg_return']:+5.2f} | {r['max_drawdown']:5.2f} | {cls} |")
    lines.append("")
    lines.append("## Best LONG (fiz_short_yur_long) per ticker")
    lines.append("")
    lines.append("| Тикер | vol_z | div_z | Горизонт | Сигн | WR% | PF | Avg% | DD% | Класс |")
    lines.append("|-------|-------|-------|----------|------|-----|----|------|------|-------|")
    long_leaders = [r for r in leaders if r['direction'] == 'LONG']
    long_leaders.sort(key=lambda x: -x['win_rate'])
    for r in long_leaders:
        cls = classify(r)
        vzt = r.get('vol_z_thr', '')
        dzt = r.get('div_z_thr', '')
        hl  = r.get('horizon_label', '-')
        lines.append(f"| {r['symbol']:6s} | {vzt:4} | {dzt:4} | {hl:>8s} | "
                     f"{r['n_signals']:4d} | {r['win_rate']:4.1f} | "
                     f"{r['profit_factor']:3.2f} | {r['avg_return']:+5.2f} | "
                     f"{r['max_drawdown']:5.2f} | {cls} |")
    lines.append("")
    lines.append("## Best SHORT (fiz_long_yur_short) per ticker")
    lines.append("")
    lines.append("| Тикер | vol_z | div_z | Горизонт | Сигн | WR% | PF | Avg% | DD% | Класс |")
    lines.append("|-------|-------|-------|----------|------|-----|----|------|------|-------|")
    short_leaders = [r for r in leaders if r['direction'] == 'SHORT']
    short_leaders.sort(key=lambda x: -x['win_rate'])
    for r in short_leaders:
        cls = classify(r)
        vzt = r.get('vol_z_thr', '')
        dzt = r.get('div_z_thr', '')
        hl  = r.get('horizon_label', '-')
        lines.append(f"| {r['symbol']:6s} | {vzt:4} | {dzt:4} | {hl:>8s} | "
                     f"{r['n_signals']:4d} | {r['win_rate']:4.1f} | "
                     f"{r['profit_factor']:3.2f} | {r['avg_return']:+5.2f} | "
                     f"{r['max_drawdown']:5.2f} | {cls} |")
    lines.append("")
    lines.append("## KEEP Tickers Summary")
    lines.append("")
    lines.append("| Тикер | LONG WR% | LONG PF | SHORT WR% | SHORT PF | Класс |")
    lines.append("|-------|----------|---------|-----------|----------|-------|")
    for sym in KEEP_TICKERS:
        l = find_best(load_results(sym), 'LONG')
        s = find_best(load_results(sym), 'SHORT')
        lwr = f"{l['win_rate']:.1f}" if l else '-'
        lpf = f"{l['profit_factor']:.2f}" if l else '-'
        swr = f"{s['win_rate']:.1f}" if s else '-'
        spf = f"{s['profit_factor']:.2f}" if s else '-'
        cls = '—'
        if l and s:
            wr = max(l['win_rate'], s['win_rate'])
            pf = max(l['profit_factor'], s['profit_factor'])
            n = max(l['n_signals'], s['n_signals'])
            cls = classify({'win_rate': wr, 'profit_factor': pf, 'n_signals': n})
        lines.append(f"| {sym:6s} | {lwr:>6s}% | {lpf:>6s} | {swr:>6s}% | {spf:>6s} | {cls} |")
    lines.append("")
    lines.append("## MAYBE Tickers Summary")
    lines.append("")
    lines.append("| Тикер | LONG WR% | LONG PF | SHORT WR% | SHORT PF | Класс |")
    lines.append("|-------|----------|---------|-----------|----------|-------|")
    for sym in MAYBE_TICKERS:
        l = find_best(load_results(sym), 'LONG')
        s = find_best(load_results(sym), 'SHORT')
        lwr = f"{l['win_rate']:.1f}" if l else '-'
        lpf = f"{l['profit_factor']:.2f}" if l else '-'
        swr = f"{s['win_rate']:.1f}" if s else '-'
        spf = f"{s['profit_factor']:.2f}" if s else '-'
        cls = '—'
        if l and s:
            wr = max(l['win_rate'], s['win_rate'])
            pf = max(l['profit_factor'], s['profit_factor'])
            n = max(l['n_signals'], s['n_signals'])
            cls = classify({'win_rate': wr, 'profit_factor': pf, 'n_signals': n})
        lines.append(f"| {sym:6s} | {lwr:>6s}% | {lpf:>6s} | {swr:>6s}% | {spf:>6s} | {cls} |")

    md_path = os.path.join(REPORTS_DIR, 'SUMMARY.md')
    with open(md_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"✅ Complete SUMMARY.md written to {md_path}")
    print(f"   {len(ALL_TICKERS)} tickers analyzed")
    print()
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
