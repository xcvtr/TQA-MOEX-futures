#!/usr/bin/env python3
"""
TF OI Rescue v3: correct timeframe OI Divergence — optimized with caching.
"""

import sys, os, json, pickle, time
from datetime import datetime, timedelta, timezone
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import psycopg2

from trading_bot.new_strategies import detect_oi_divergence_signals
from trading_bot.strategy_cascade import compute_quality_score
from trading_bot.filters import calc_atr, calc_adx

from scripts.bar_level_sim import (
    BarLevelPortfolio, _calc_pnl, _max_drawdown,
    TICKER_CONFIGS, TICKER_PRIORITY, TICKER_TO_GROUP,
    TICKER_PRIORITY_WEIGHT, MAX_WEIGHT, CORRELATION_GROUPS, SECTOR_CAP,
)

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='postgres')
REPORT_DIR = 'reports'
TODAY = datetime.now().strftime('%Y-%m-%d')
REPORT_PATH = os.path.join(REPORT_DIR, f'{TODAY}-tf-oi-rescue-v4.md')
ALL_TICKERS = sorted(TICKER_CONFIGS.keys())
DAYS = 365

BASELINE_PARAMS = dict(
    initial_capital=100000, max_dd=0.20, margin_usage=0.10,
    max_concurrent=8, total_margin_limit=0.20, stop_loss_pct=0.02,
    use_score_sizing=True, use_score_eviction=True, atr_stop_mult=2.0,
    use_score_decay=True, use_mtm=True, use_trailing=False, trailing_mult=3.0,
)

TF_CONFIGS = {
    'H1':  {'resample_rule': '1h', 'params_grid': [
        {'lookback': 20, 'extreme_window': 10, 'horizon': 12, 'bear_threshold': 0.85, 'bull_threshold': 1.15, 'min_gap_bars': 0},
        {'lookback': 20, 'extreme_window': 10, 'horizon': 12, 'bear_threshold': 0.90, 'bull_threshold': 1.10, 'min_gap_bars': 12},
        {'lookback': 20, 'extreme_window': 10, 'horizon': 12, 'bear_threshold': 0.85, 'bull_threshold': 1.15, 'min_gap_bars': 12},
    ]},
}

SCORE_THRESHOLDS = [0.0, 0.3]
ADX_THRESHOLDS = [0, 20, 25]

# Portfolio variants to try
PORTFOLIO_VARIANTS = {
    'baseline': dict(max_concurrent=8, use_score_eviction=True, use_trailing=False, max_hold_bars=30),
    'highcap':  dict(max_concurrent=20, use_score_eviction=False, use_trailing=False, max_hold_bars=60),
    'trail':    dict(max_concurrent=8, use_score_eviction=True, use_trailing=True, trailing_mult=3.0, max_hold_bars=60),
    'noroll':   dict(max_concurrent=8, use_score_eviction=False, use_trailing=True, trailing_mult=3.0, max_hold_bars=40, allow_rollover=False),
}


def load_all_data(days=365):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    all_data = {}
    conn = psycopg2.connect(**DB)
    try:
        for sym in ALL_TICKERS:
            t0 = time.time()
            cur = conn.cursor()
            cur.execute(
                "SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s ORDER BY time",
                (sym, since))
            ohlcv = [{'time': r[0], 'open': float(r[1]), 'high': float(r[2]),
                      'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in cur]
            cur.close()
            if not ohlcv or len(ohlcv) < 100:
                print(f"    {sym}: skip OHLCV ({len(ohlcv)})")
                continue
            cur = conn.cursor()
            cur.execute(
                "SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time",
                (sym, since))
            oi_rows = [{'time': r[0], 'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]),
                        'yur_buy': float(r[3]), 'yur_sell': float(r[4]), 'total_oi': float(r[5])} for r in cur]
            cur.close()
            if not oi_rows:
                print(f"    {sym}: skip OI")
                continue
            oi_map = {r['time'].strftime('%Y-%m-%d %H:%M'): r for r in oi_rows}
            merged = []
            for r in ohlcv:
                t_str = r['time'].strftime('%Y-%m-%d %H:%M')
                o = oi_map.get(t_str)
                if o is None:
                    continue
                merged.append({
                    'time': r['time'], 'open': r['open'], 'high': r['high'],
                    'low': r['low'], 'close': r['close'], 'volume': r['volume'],
                    'total_oi': o['total_oi'], 'fiz_buy': o['fiz_buy'], 'fiz_sell': o['fiz_sell'],
                    'yur_buy': o['yur_buy'], 'yur_sell': o['yur_sell'], 'symbol': sym,
                })
            if len(merged) < 100:
                print(f"    {sym}: skip merged ({len(merged)})")
                continue
            all_data[sym] = merged
            print(f"    {sym}: {len(merged)} bars, {time.time()-t0:.1f}s")
    finally:
        conn.close()
    print(f"  Loaded {len(all_data)}/{len(ALL_TICKERS)}")
    return all_data


def build_ohlcv_cache(all_data):
    cache = {}
    for sym, rows in all_data.items():
        df = pd.DataFrame({'close': [r['close'] for r in rows]},
                          index=pd.DatetimeIndex([r['time'] for r in rows]))
        cache[sym] = df
    with open('.ohlcv_cache.pkl', 'wb') as f:
        pickle.dump(cache, f)
    print(f"  Saved .ohlcv_cache.pkl: {len(cache)} tickers")


def resample_merged(merged, rule):
    records = [{'time': r['time'], 'open': r['open'], 'high': r['high'], 'low': r['low'],
                'close': r['close'], 'volume': r['volume'], 'total_oi': r['total_oi'],
                'fiz_buy': r['fiz_buy'], 'fiz_sell': r['fiz_sell'],
                'yur_buy': r['yur_buy'], 'yur_sell': r['yur_sell']} for r in merged]
    df = pd.DataFrame(records)
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
           'total_oi': 'last', 'fiz_buy': 'last', 'fiz_sell': 'last',
           'yur_buy': 'last', 'yur_sell': 'last'}
    resampled = df.resample(rule).agg(agg).dropna(subset=['close'])
    sig = merged[0].get('symbol', '?')
    out = []
    for idx, row in resampled.iterrows():
        out.append({
            'time': idx, 'open': float(row['open']), 'high': float(row['high']),
            'low': float(row['low']), 'close': float(row['close']),
            'volume': float(row['volume']), 'total_oi': float(row['total_oi']),
            'fiz_buy': float(row['fiz_buy']), 'fiz_sell': float(row['fiz_sell']),
            'yur_buy': float(row['yur_buy']), 'yur_sell': float(row['yur_sell']),
            'symbol': sig,
        })
    return out


def prepare_tf_data(all_data, tf_name, tf_config):
    """Pre-resample all tickers for a TF. Returns {sym: resampled_rows}."""
    tf_data = {}
    for sym, merged in all_data.items():
        resampled = resample_merged(merged, tf_config['resample_rule'])
        if len(resampled) >= 50:
            tf_data[sym] = resampled
    return tf_data


def detect_scored_signals(tf_data, tf_config, score_thresh, adx_threshold=0):
    """Detect OI divergence signals on pre-resampled data, filter by score and ADX."""
    all_sigs = []

    # Precompute indicators per ticker
    atr_cache = {}
    adx_cache = {}

    for sym, resampled in tf_data.items():
        closes = [r['close'] for r in resampled]
        highs = [r['high'] for r in resampled]
        lows = [r['low'] for r in resampled]
        try:
            atr_cache[sym] = calc_atr(highs, lows, closes, 14)
        except Exception:
            atr_cache[sym] = []
        try:
            adx_cache[sym] = calc_adx(closes, 14)
        except Exception:
            adx_cache[sym] = []

    for sym, resampled in tf_data.items():
        for p in tf_config['params_grid']:
            sigs = detect_oi_divergence_signals(resampled, {
                'lookback': p['lookback'], 'extreme_window': p['extreme_window'],
                'horizon': p['horizon'], 'bear_threshold': p.get('bear_threshold', 0.85),
                'bull_threshold': p.get('bull_threshold', 1.15), 'min_gap_bars': p.get('min_gap_bars', 0),
            })
            for s in sigs:
                idx = s.get('idx')
                if idx is None or idx >= len(resampled):
                    continue
                try:
                    quality = compute_quality_score(resampled, idx)
                    score = quality['total']
                except Exception:
                    score = 0.3
                if score < score_thresh:
                    continue
                s['score'] = score
                s['ticker'] = sym
                s['time'] = str(resampled[idx]['time'])
                atr_vals = atr_cache.get(sym, [])
                if atr_vals and idx < len(atr_vals) and resampled[idx]['close'] > 0:
                    s['atr_pct'] = atr_vals[idx] / resampled[idx]['close']
                else:
                    s['atr_pct'] = 0
                adx_vals = adx_cache.get(sym, [])
                s['adx_value'] = adx_vals[idx] if adx_vals and idx < len(adx_vals) else 0
                if adx_threshold > 0 and s['adx_value'] < adx_threshold:
                    continue
                s['_param_horizon'] = p['horizon']
                s['_param_lookback'] = p['lookback']
                all_sigs.append(s)

    return all_sigs


def compute_signals_per_day(signals):
    days = set(s.get('time', '')[:10] for s in signals)
    return len(signals) / len(days) if days else 0


def compute_wr(trades):
    if not trades:
        return 0.0
    return sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100


def analyze_exit_reasons(trades):
    reasons = Counter(t['exit_reason'] for t in trades)
    return reasons, {k: v / len(trades) * 100 for k, v in reasons.items()}


def run_walkforward(signals, params):
    n = len(signals)
    n4 = n // 4
    folds = [signals[:n4], signals[n4:2*n4], signals[2*n4:3*n4], signals[3*n4:]]
    fold_results = []
    for fs in folds:
        groups = {}
        for s in fs:
            t = s['_time_dt']
            groups.setdefault(t, []).append(s)
        sorted_times = sorted(groups.keys())
        p = BarLevelPortfolio(**params)
        r = p._run_grouped(sorted_times, groups)
        fold_results.append(r)
    return fold_results


def run_all():
    t_start = time.time()
    print("=" * 60)
    print(f"  TF OI Rescue v4 — {TODAY}")
    print(f"  Tickers: {len(ALL_TICKERS)}")
    print("=" * 60)

    print("\n[1/4] Loading data...")
    all_data = load_all_data(DAYS)

    print("\n[2/4] Building OHLCV cache...")
    build_ohlcv_cache(all_data)
    BarLevelPortfolio._signals_cache = None
    BarLevelPortfolio._ohlcv_np = None
    BarLevelPortfolio._grouped_cache = None

    # Pre-resample data for H1 only
    print("\n  Pre-resampling data per TF...")
    tf_data_cache = {}
    for tf_name in ['H1']:
        t0 = time.time()
        tf_data_cache[tf_name] = prepare_tf_data(all_data, tf_name, TF_CONFIGS[tf_name])
        print(f"    {tf_name}: {len(tf_data_cache[tf_name])} tickers, {time.time()-t0:.0f}s")

    print("\n[3/4] Detecting signals + running portfolio...")
    tf_results = {}
    best_overall = None

    for tf_name in ['H1']:
        tf_config = TF_CONFIGS[tf_name]
        tf_data = tf_data_cache[tf_name]
        print(f"\n  ═══ {tf_name} ═══")

        # Generate signals once at score=0 (all params, no ADX), then re-filter
        t0 = time.time()
        all_sigs = detect_scored_signals(tf_data, tf_config, 0.0, adx_threshold=0)
        print(f"    Raw signals (no ADX): {len(all_sigs)} ({time.time()-t0:.0f}s)")

        if not all_sigs:
            continue

        for adx_th in ADX_THRESHOLDS:
            # Filter by ADX
            if adx_th > 0:
                sigs_adx = [s for s in all_sigs if s['adx_value'] >= adx_th]
            else:
                sigs_adx = all_sigs
            if len(sigs_adx) < 5:
                continue

            for score_thresh in SCORE_THRESHOLDS:
                if score_thresh > 0:
                    sigs = [s for s in sigs_adx if s['score'] >= score_thresh]
                else:
                    sigs = sigs_adx
                if len(sigs) < 5:
                    continue

                sigs_per_day = compute_signals_per_day(sigs)
                for s in sigs:
                    s['_time_dt'] = pd.Timestamp(s['time'])

                for pv_name, pv_params in PORTFOLIO_VARIANTS.items():
                    adx_tag = f"adx≥{adx_th}" if adx_th > 0 else ""
                    label = f"{tf_name} {adx_tag} s≥{score_thresh} {pv_name}".strip()
                    params = dict(BASELINE_PARAMS)
                    params.update(pv_params)

                    p = BarLevelPortfolio(**params)
                    result = p.run(sigs)
                    wr = compute_wr(result['trades'])
                    reasons, pcts = analyze_exit_reasons(result['trades'])
                    rollover_pct = pcts.get('rollover', 0)

                    print(f"    [{label}] sigs={len(sigs)} sig/d={sigs_per_day:.1f} ret={result['total_return_pct']:.2f}% DD={result['max_dd_pct']:.2f}% Calmar={result['calmar']:.4f} trades={len(result['trades'])} WR={wr:.1f}% roll={rollover_pct:.0f}%")

                    entry = {
                        'label': label, 'tf': tf_name, 'score_thresh': score_thresh,
                        'adx_thresh': adx_th, 'portfolio': pv_name,
                        'num_signals': len(sigs), 'signals_per_day': sigs_per_day,
                        'return': result['total_return_pct'], 'dd': result['max_dd_pct'],
                        'calmar': result['calmar'], 'trades': len(result['trades']),
                        'wr': wr, 'rollover_pct': rollover_pct,
                        'exit_reasons': dict(reasons), 'result': result, 'signals': sigs,
                    }
                    tf_results[label] = entry
                    if best_overall is None or entry['calmar'] > best_overall['calmar']:
                        best_overall = entry

    # Walk-forward
    print("\n[4/4] Walk-forward...")
    wf_results = {}
    if best_overall and best_overall['calmar'] > 1.0 and best_overall['trades'] >= 10:
        print(f"\n  Best: {best_overall['label']} (Calmar={best_overall['calmar']:.4f})")
        params = dict(BASELINE_PARAMS)
        params.update(PORTFOLIO_VARIANTS[best_overall['portfolio']])
        wf = run_walkforward(best_overall['signals'], params)
        wf_results[best_overall['label']] = wf
        for i, r in enumerate(wf):
            print(f"    Fold {i+1}: ret={r['total_return_pct']:.2f}% DD={r['max_dd_pct']:.2f}% Calmar={r['calmar']:.4f} trades={len(r['trades'])}")
        print(f"    All folds profitable: {all(r['total_return_pct'] > 0 for r in wf)}")
    else:
        reason = "no best" if not best_overall else f"Calmar={best_overall['calmar']:.4f} < 1 or trades={best_overall['trades']} < 10"
        print(f"\n  Skip W-F: {reason}")

    # Report
    print("\n═══ REPORT ═══")
    os.makedirs(REPORT_DIR, exist_ok=True)
    report = [f"# TF OI Rescue v4 — {TODAY}",
              "", f"Time: {datetime.now()}", f"Tickers: {len(ALL_TICKERS)}", f"Data: {DAYS}d",
              "", "## All Results",
              "", "| Label | Sig# | Sig/d | Return% | DD% | Calmar | Trades | WR% | Roll% |",
              "|-------|---------|-------|---------|-----|--------|--------|-----|-------|"]
    sorted_results = sorted(tf_results.values(), key=lambda e: e['calmar'], reverse=True)
    for e in sorted_results:
        report.append(f"| {e['label']} | {e['num_signals']} | {e['signals_per_day']:.1f} | {e['return']:.2f} | {e['dd']:.2f} | {e['calmar']:.4f} | {e['trades']} | {e['wr']:.1f} | {e['rollover_pct']:.0f} |")
    report.append("")

    if best_overall:
        e = best_overall
        report.append("## Best: " + e['label'])
        report.append("")
        report.append("| Metric | Value |")
        report.append("|--------|-------|")
        report.append(f"| Return | {e['return']:.2f}% |")
        report.append(f"| Max DD | {e['dd']:.2f}% |")
        report.append(f"| Calmar | {e['calmar']:.4f} |")
        report.append(f"| Total signals | {e['num_signals']} |")
        report.append(f"| Signals/day | {e['signals_per_day']:.2f} |")
        report.append(f"| Trades (portfolio) | {e['trades']} |")
        report.append(f"| Win Rate | {e['wr']:.1f}% |")
        report.append(f"| Rollover % | {e['rollover_pct']:.1f}% |")
        report.append("")
        if best_overall['label'] in wf_results:
            wf = wf_results[best_overall['label']]
            report.append("### Walk-Forward (4 folds)")
            report.append("")
            report.append("| Fold | Return% | DD% | Calmar | Trades |")
            report.append("|------|---------|-----|--------|--------|")
            for i, r in enumerate(wf):
                report.append(f"| {i+1} | {r['total_return_pct']:.2f} | {r['max_dd_pct']:.2f} | {r['calmar']:.4f} | {len(r['trades'])} |")
            report.append("")
            ok = all(r['total_return_pct'] > 0 for r in wf)
            report.append(f"**All folds profitable:** {ok}")
            report.append("**Stability:** ✅ PASS" if ok else "**Stability:** ❌ FAIL")
            report.append("")

        ticker_counts = Counter(s['ticker'] for s in best_overall['signals'])
        report.append("## Top Tickers (Best)")
        report.append("")
        report.append("| Ticker | Signals |")
        report.append("|--------|---------|")
        for tk, cnt in ticker_counts.most_common(15):
            report.append(f"| {tk} | {cnt} |")
        report.append("")

    report.append(f"\nTime: {time.time()-t_start:.0f}s")
    with open(REPORT_PATH, 'w') as f:
        f.write('\n'.join(report))
    print(f"\n✅ Report: {REPORT_PATH}")
    print(f"⏱  Total: {time.time()-t_start:.0f}s")

    print("\n═══ RANKED ═══")
    for e in sorted_results[:6]:
        print(f"  {e['label']:<40} ret={e['return']:>8.2f}%  DD={e['dd']:>6.2f}%  Calmar={e['calmar']:.4f}  T={e['trades']}  R={e['rollover_pct']:.0f}%")

    return tf_results


if __name__ == '__main__':
    run_all()
