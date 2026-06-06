#!/usr/bin/env python3
"""Phase 2: Detailed analysis of KEEP/MAYBE tickers — Volume Surge + FIZ/YUR Divergence.
Optimizes thresholds, exit horizons, session analysis, seasonality.
Saves per-ticker reports and a global SUMMARY.md."""

import psycopg2, sys, os, csv, math
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────────
DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')

KEEP_TICKERS = ['GL','AF','CC','CE','DX','HS','HY','MC','MG','NG','NM','NR',
                'OJ','PD','SE','SF','SN','SP','SS','TN','TT','W4','YD','AL']
MAYBE_TICKERS = ['BM','GK','IB','KC','ME','MM','PT','RN','SV','VB']
ALL_TICKERS = KEEP_TICKERS + MAYBE_TICKERS

VOL_Z_THRESHOLDS  = [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]
DIV_Z_THRESHOLDS  = [0.5, 0.75, 1.0, 1.25, 1.5]
EXIT_HORIZONS     = [3, 6, 12, 24, 48]  # 5m bars -> 15m, 30m, 1h, 2h, 4h

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports', 'phase2')

# Session windows in MSK (UTC+3)
SESSION_DEFS = [
    ('morning',  10*60, 13*60),   # 10:00 - 13:00
    ('afternoon',13*60, 17*60),   # 13:00 - 17:00
    ('evening',  17*60, 19*60),   # 17:00 - 19:00
]

# ── Helpers ─────────────────────────────────────────────────────────────────
def zs(vals, w=20):
    """Rolling z-score, w preceding values (no look-ahead)."""
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x - mu)**2 for x in chunk) / w
        sd = var ** 0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out

def load_data(symbol):
    """Load joined 5m data for symbol from 2023-01-01."""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT oi.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
               p.close, p.volume, p.open as next_open
        FROM moex_prices_5m_oi oi
        JOIN moex_prices_5m p ON p.symbol=oi.symbol AND p.time=oi.time
        WHERE oi.symbol=%s AND oi.time >= '2023-01-01'
        ORDER BY oi.time
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def time_to_minutes(ts):
    """Convert timestamp to minutes since midnight (MSK = UTC+3)."""
    return ts.hour * 60 + ts.minute

def get_session_label(minutes):
    for name, start, end in SESSION_DEFS:
        if start <= minutes < end:
            return name
    return 'other'

def calc_drawdown(returns_pct):
    """Maximum peak-to-trough drawdown of cumulative returns series."""
    if not returns_pct:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns_pct:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd

def analyze_ticker(symbol):
    """Full analysis for one ticker. Returns list of result dicts."""
    rows = load_data(symbol)
    if len(rows) < 500:
        print(f"  ⚠ {symbol}: only {len(rows)} rows, insufficient")
        return []

    # Build feature arrays (all floats)
    times  = [r[0] for r in rows]
    close  = [float(r[5] or 0.0) for r in rows]
    volume = [float(r[6] or 0.0) for r in rows]
    open_px= [float(r[7] or 0.0) for r in rows]
    fiz_net = [float((r[1] or 0) - (r[2] or 0)) for r in rows]
    yur_net = [float((r[3] or 0) - (r[4] or 0)) for r in rows]

    n = len(rows)
    vol_z = zs(volume, 20)
    fiz_z = zs(fiz_net, 20)
    yur_z = zs(yur_net, 20)

    results = []  # list of dicts

    for vzt in VOL_Z_THRESHOLDS:
        for dzt in DIV_Z_THRESHOLDS:
            directions = {
                'LONG':  {'cond': lambda fz, yz: fz < 0 and yz > 0,
                          'label': 'fiz_short_yur_long'},
                'SHORT': {'cond': lambda fz, yz: fz > 0 and yz < 0,
                          'label': 'fiz_long_yur_short'},
            }
            for dir_name, dir_info in directions.items():
                # Collect signals
                sig_returns = []       # list of returns (pct) per signal
                sig_ret_series = []    # list of list of per-bar returns for drawdown
                sig_sessions = defaultdict(list)
                sig_months   = defaultdict(int)

                # Bar-by-bar scan (need window=20 warmup + horizon future)
                max_hor = max(EXIT_HORIZONS)
                for i in range(20, n - max_hor - 1):
                    if vol_z[i] < vzt:
                        continue
                    fzi, yzi = fiz_z[i], yur_z[i]
                    if abs(fzi) < dzt or abs(yzi) < dzt:
                        continue
                    if fzi * yzi >= 0:
                        continue
                    if not dir_info['cond'](fzi, yzi):
                        continue

                    # Entry: open of next bar
                    entry_px = open_px[i + 1]
                    if entry_px <= 0 or close[i] <= 0:
                        continue

                    # Evaluate all horizons
                    for h in EXIT_HORIZONS:
                        if i + 1 + h >= n:
                            continue
                        exit_px = close[i + 1 + h - 1]  # close of bar at offset h
                        if exit_px <= 0:
                            continue
                        if dir_name == 'SHORT':
                            ret = (entry_px - exit_px) / entry_px * 100.0
                        else:
                            ret = (exit_px - entry_px) / entry_px * 100.0

                        # Per-bar returns for drawdown calc
                        bar_rets = []
                        for j in range(1, h + 1):
                            px = close[i + j] if j < h else exit_px
                            if dir_name == 'SHORT':
                                br = (entry_px - px) / entry_px * 100.0
                            else:
                                br = (px - entry_px) / entry_px * 100.0
                            bar_rets.append(br)

                        sig_returns.append((h, ret))
                        sig_ret_series.append((h, bar_rets))

                        # Session label
                        bar_min = time_to_minutes(times[i + 1])
                        sess = get_session_label(bar_min)
                        sig_sessions[(h, sess)].append(ret)

                        # Month label
                        month_key = times[i + 1].strftime('%Y-%m')
                        sig_months[(h, month_key)] = sig_months.get((h, month_key), 0) + 1

                if not sig_returns:
                    continue

                # Group by horizon
                for h in EXIT_HORIZONS:
                    h_rets = [r for (hh, r) in sig_returns if hh == h]
                    if len(h_rets) < 5:
                        continue

                    wins   = sum(1 for r in h_rets if r > 0)
                    losses = sum(1 for r in h_rets if r <= 0)
                    wr     = wins / len(h_rets) * 100.0
                    gains  = sum(r for r in h_rets if r > 0)
                    loss_sum = abs(sum(r for r in h_rets if r < 0))
                    pf     = gains / loss_sum if loss_sum > 0 else (99.9 if gains > 0 else 0.0)
                    avg_ret = sum(h_rets) / len(h_rets)
                    dd     = calc_drawdown(h_rets)

                    # Session breakdown
                    sess_breakdown = {}
                    for (hh, sess), rets in sig_sessions.items():
                        if hh != h:
                            continue
                        sess_wr = sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else 0
                        sess_avg = sum(rets) / len(rets) if rets else 0
                        sess_breakdown[sess] = {'n': len(rets), 'wr': round(sess_wr, 1), 'avg': round(sess_avg, 3)}

                    # Month distribution
                    month_dist = {}
                    for (hh, mk), cnt in sig_months.items():
                        if hh == h:
                            month_dist[mk] = cnt

                    results.append({
                        'symbol': symbol,
                        'vol_z_thr': vzt,
                        'div_z_thr': dzt,
                        'direction': dir_name,
                        'dir_label': dir_info['label'],
                        'horizon': h,
                        'horizon_label': f'{h*5}m',
                        'n_signals': len(h_rets),
                        'win_rate': round(wr, 1),
                        'profit_factor': round(pf, 2),
                        'avg_return': round(avg_ret, 3),
                        'max_drawdown': round(dd, 3),
                        'gains': round(gains, 2),
                        'losses': round(loss_sum, 2),
                        'wins': wins,
                        'losses': losses,
                        'session_breakdown': sess_breakdown,
                        'month_distribution': month_dist,
                    })

    return results

# ── Reporting ────────────────────────────────────────────────────────────────
def save_results(symbol, results):
    """Save per-ticker summary and CSV."""
    sym_dir = os.path.join(REPORTS_DIR, symbol)
    os.makedirs(sym_dir, exist_ok=True)

    if not results:
        # Write empty marker
        with open(os.path.join(sym_dir, 'summary.txt'), 'w') as f:
            f.write(f"{symbol}: NO SIGNALS FOUND\n")
        with open(os.path.join(sym_dir, 'details.csv'), 'w') as f:
            f.write("symbol,vol_z_thr,div_z_thr,direction,horizon,n_signals,win_rate,profit_factor,avg_return,max_drawdown\n")
        return

    # Find best params per direction
    best = {}
    for r in results:
        key = (r['direction'], r['dir_label'])
        if key not in best:
            best[key] = r
        else:
            cur = best[key]
            # Prefer: high win rate, then high PF, then many signals
            if (r['win_rate'] > cur['win_rate'] or
                (r['win_rate'] == cur['win_rate'] and r['profit_factor'] > cur['profit_factor']) or
                (r['win_rate'] == cur['win_rate'] and r['profit_factor'] == cur['profit_factor'] and r['n_signals'] > cur['n_signals'])):
                best[key] = r

    # Summary text
    summary_lines = []
    summary_lines.append(f"{'='*60}")
    summary_lines.append(f"  {symbol} — PHASE 2 ANALYSIS")
    summary_lines.append(f"{'='*60}")
    summary_lines.append(f"")
    summary_lines.append(f"Total configurations tested: {len(results)}")
    summary_lines.append(f"")

    for (dir_name, dir_label), r in best.items():
        summary_lines.append(f"── {dir_name} ({dir_label}) ──")
        summary_lines.append(f"  Best params:  vol_z ≥ {r['vol_z_thr']}, |div_z| ≥ {r['div_z_thr']}, horizon = {r['horizon_label']}")
        summary_lines.append(f"  Signals:      {r['n_signals']}")
        summary_lines.append(f"  Win Rate:     {r['win_rate']:.1f}%")
        summary_lines.append(f"  Profit Factor:{r['profit_factor']:.2f}")
        summary_lines.append(f"  Avg Return:   {r['avg_return']:+.3f}%")
        summary_lines.append(f"  Max Drawdown: {r['max_drawdown']:.3f}%")
        summary_lines.append(f"  Sessions:")
        for sess in ['morning', 'afternoon', 'evening']:
            if sess in r['session_breakdown']:
                sd = r['session_breakdown'][sess]
                summary_lines.append(f"    {sess:12s}: n={sd['n']:4d} wr={sd['wr']:5.1f}% avg={sd['avg']:+7.3f}%")
        summary_lines.append(f"  Top-5 months by signal count:")
        top_months = sorted(r['month_distribution'].items(), key=lambda x: -x[1])[:5]
        for mk, cnt in top_months:
            summary_lines.append(f"    {mk}: {cnt} signals")
        summary_lines.append(f"")

    with open(os.path.join(sym_dir, 'summary.txt'), 'w') as f:
        f.write('\n'.join(summary_lines))
    print('\n'.join(summary_lines))

    # CSV details — flat record
    csv_path = os.path.join(sym_dir, 'details.csv')
    fieldnames = [
        'symbol','vol_z_thr','div_z_thr','direction','dir_label',
        'horizon','horizon_label','n_signals','win_rate','profit_factor',
        'avg_return','max_drawdown','gains','losses','wins','losses'
    ]
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"  → {csv_path} ({len(results)} rows)")


def build_global_summary(all_ticker_results):
    """Build SUMMARY.md with leaderboard."""
    os.makedirs(REPORTS_DIR, exist_ok=True)

    # Collect best per direction per ticker
    leaders = []
    for symbol, results in all_ticker_results.items():
        if not results:
            leaders.append({
                'symbol': symbol, 'direction': 'LONG', 'dir_label': 'fiz_short_yur_long',
                'n_signals': 0, 'win_rate': 0, 'profit_factor': 0, 'avg_return': 0,
                'max_drawdown': 0, 'gains': 0, 'losses': 0
            })
            leaders.append({
                'symbol': symbol, 'direction': 'SHORT', 'dir_label': 'fiz_long_yur_short',
                'n_signals': 0, 'win_rate': 0, 'profit_factor': 0, 'avg_return': 0,
                'max_drawdown': 0, 'gains': 0, 'losses': 0
            })
            continue
        best = {}
        for r in results:
            key = r['direction']
            if key not in best:
                best[key] = r
            else:
                cur = best[key]
                if (r['win_rate'] > cur['win_rate'] or
                    (r['win_rate'] == cur['win_rate'] and r['profit_factor'] > cur['profit_factor'])):
                    best[key] = r
        for dir_name in ['LONG', 'SHORT']:
            if dir_name in best:
                leaders.append(best[dir_name])
            else:
                leaders.append({
                    'symbol': symbol, 'direction': dir_name, 'dir_label': '',
                    'n_signals': 0, 'win_rate': 0, 'profit_factor': 0, 'avg_return': 0,
                    'max_drawdown': 0, 'gains': 0, 'losses': 0
                })

    # Sort: best win rate descending
    leaders.sort(key=lambda x: -x['win_rate'])

    # Classify
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

    lines = []
    lines.append(f"# Phase 2 — Volume Surge + FIZ/YUR Divergence Analysis")
    lines.append(f"")
    lines.append(f"**Generated:** $(date)")
    lines.append(f"")
    lines.append(f"## Leaderboard")
    lines.append(f"")
    lines.append(f"| Тикер | Напр. | Сигн | WR% | PF | Avg% | DD% | Класс |")
    lines.append(f"|-------|-------|------|-----|----|------|-----|-------|")

    for r in leaders:
        cls = classify(r)
        lines.append(f"| {r['symbol']:6s} | {r['direction']:5s} | {r['n_signals']:4d} | "
                     f"{r['win_rate']:4.1f} | {r['profit_factor']:3.2f} | "
                     f"{r['avg_return']:+5.2f} | {r['max_drawdown']:5.2f} | {cls} |")

    lines.append(f"")
    lines.append(f"## Best LONG (fiz_short_yur_long)")
    lines.append(f"")
    lines.append(f"| Тикер | vol_z | div_z | Горизонт | Сигн | WR% | PF | Avg% | DD% |")
    lines.append(f"|-------|-------|-------|----------|------|-----|----|------|-----|")
    for r in sorted(leaders, key=lambda x: -x['win_rate']):
        if r['direction'] != 'LONG' or r['n_signals'] == 0:
            continue
        lines.append(f"| {r['symbol']:6s} | {r.get('vol_z_thr','-'):4} | {r.get('div_z_thr','-'):4} | "
                     f"{r.get('horizon_label','?')} | {r['n_signals']:4d} | "
                     f"{r['win_rate']:4.1f} | {r['profit_factor']:3.2f} | "
                     f"{r['avg_return']:+5.2f} | {r['max_drawdown']:5.2f} |")

    lines.append(f"")
    lines.append(f"## Best SHORT (fiz_long_yur_short)")
    lines.append(f"")
    lines.append(f"| Тикер | vol_z | div_z | Горизонт | Сигн | WR% | PF | Avg% | DD% |")
    lines.append(f"|-------|-------|-------|----------|------|-----|----|------|-----|")
    for r in sorted(leaders, key=lambda x: -x['win_rate']):
        if r['direction'] != 'SHORT' or r['n_signals'] == 0:
            continue
        lines.append(f"| {r['symbol']:6s} | {r.get('vol_z_thr','-'):4} | {r.get('div_z_thr','-'):4} | "
                     f"{r.get('horizon_label','?')} | {r['n_signals']:4d} | "
                     f"{r['win_rate']:4.1f} | {r['profit_factor']:3.2f} | "
                     f"{r['avg_return']:+5.2f} | {r['max_drawdown']:5.2f} |")

    md_path = os.path.join(REPORTS_DIR, 'SUMMARY.md')
    with open(md_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\n📊 Global summary: {md_path}")
    return lines


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    # Determine ticker list
    if len(sys.argv) > 1:
        tickers = [t.upper() for t in sys.argv[1:]]
    else:
        tickers = KEEP_TICKERS  # default: all KEEP

    print(f"🚀 Phase 2 Analysis — {len(tickers)} tickers")
    print(f"   Thresholds: vol_z={VOL_Z_THRESHOLDS}, div_z={DIV_Z_THRESHOLDS}")
    print(f"   Horizons: {EXIT_HORIZONS}")
    print()

    all_results = {}
    for i, sym in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] {sym} ...")
        try:
            res = analyze_ticker(sym)
            all_results[sym] = res
            save_results(sym, res)
        except Exception as e:
            print(f"  ❌ {sym} ERROR: {e}")
            import traceback
            traceback.print_exc()
            all_results[sym] = []
        print()

    # Build global summary
    lines = build_global_summary(all_results)
    print()
    print("✅ Done. All results in reports/phase2/")
    print('\n'.join(lines[-20:]))


if __name__ == '__main__':
    main()
