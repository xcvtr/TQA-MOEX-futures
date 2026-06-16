#!/usr/bin/env python3
"""TF OI Rescue v5: H1 noroll with relaxed params for more trades."""
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
    BarLevelPortfolio, TICKER_CONFIGS, TICKER_PRIORITY, TICKER_TO_GROUP,
    TICKER_PRIORITY_WEIGHT, MAX_WEIGHT, SECTOR_CAP,
)

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='')
REPORT_DIR = 'reports'
TODAY = datetime.now().strftime('%Y-%m-%d')
ALL_TICKERS = sorted(TICKER_CONFIGS.keys())
DAYS = 365

# ── Single param set for v5 ──────────────────────────────────────────────────
PARAM_CFG = dict(
    lookback=20, extreme_window=10, horizon=12,
    bear_threshold=0.90, bull_threshold=1.10, min_gap_bars=6,
)

# ── Data loading ─────────────────────────────────────────────────────────────

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
                continue
            cur = conn.cursor()
            cur.execute(
                "SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time",
                (sym, since))
            oi_rows = [{'time': r[0], 'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]),
                        'yur_buy': float(r[3]), 'yur_sell': float(r[4]), 'total_oi': float(r[5])} for r in cur]
            cur.close()
            oi_map = {r['time'].strftime('%Y-%m-%d %H:%M'): r for r in oi_rows}
            merged = []
            for r in ohlcv:
                t_str = r['time'].strftime('%Y-%m-%d %H:%M')
                o = oi_map.get(t_str)
                if o is None: continue
                merged.append({'time': r['time'], 'open': r['open'], 'high': r['high'],
                               'low': r['low'], 'close': r['close'], 'volume': r['volume'],
                               'total_oi': o['total_oi'], 'fiz_buy': o['fiz_buy'], 'fiz_sell': o['fiz_sell'],
                               'yur_buy': o['yur_buy'], 'yur_sell': o['yur_sell'], 'symbol': sym})
            if len(merged) < 100: continue
            all_data[sym] = merged
            print(f"  {sym}: {len(merged)} bars, {time.time()-t0:.1f}s")
    finally:
        conn.close()
    print(f"  Loaded {len(all_data)}/{len(ALL_TICKERS)} tickers")
    return all_data

def resample_merged(merged, rule='1h'):
    df = pd.DataFrame([{'time': r['time'], 'open': r['open'], 'high': r['high'],
        'low': r['low'], 'close': r['close'], 'volume': r['volume'],
        'total_oi': r['total_oi'], 'fiz_buy': r['fiz_buy'], 'fiz_sell': r['fiz_sell'],
        'yur_buy': r['yur_buy'], 'yur_sell': r['yur_sell']} for r in merged])
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
           'total_oi': 'last', 'fiz_buy': 'last', 'fiz_sell': 'last',
           'yur_buy': 'last', 'yur_sell': 'last'}
    resampled = df.resample(rule).agg(agg).dropna(subset=['close'])
    sym = merged[0].get('symbol', '?')
    out = []
    for idx, row in resampled.iterrows():
        out.append({'time': idx, 'open': float(row['open']), 'high': float(row['high']),
            'low': float(row['low']), 'close': float(row['close']), 'volume': float(row['volume']),
            'total_oi': float(row['total_oi']), 'fiz_buy': float(row['fiz_buy']),
            'fiz_sell': float(row['fiz_sell']), 'yur_buy': float(row['yur_buy']),
            'yur_sell': float(row['yur_sell']), 'symbol': sym})
    return out

# ── Detect H1 signals ────────────────────────────────────────────────────────

def detect_scored_signals(tf_data):
    """Detect OI divergence with v5 params, NO score filter, NO ADX filter."""
    all_sigs = []
    for sym, resampled in tf_data.items():
        if len(resampled) < 50:
            continue
        sigs = detect_oi_divergence_signals(resampled, PARAM_CFG)
        for s in sigs:
            idx = s.get('idx')
            if idx is None or idx >= len(resampled):
                continue
            s['ticker'] = sym
            s['time'] = str(resampled[idx]['time'])
            s['score'] = 0.5  # neutral
            s['atr_pct'] = 0.01
            s['adx_value'] = 20
            s['_param_horizon'] = PARAM_CFG['horizon']
            all_sigs.append(s)
    return all_sigs

def compute_signals_per_day(signals):
    days = set(s.get('time', '')[:10] for s in signals)
    return len(signals) / len(days) if days else 0

def compute_wr(trades):
    if not trades: return 0.0
    return sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100

# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    t_start = time.time()
    print("=" * 60)
    print(f"  TF OI Rescue v5 — {TODAY}")
    print(f"  Params: {PARAM_CFG}")
    print("=" * 60)

    print("\n[1/3] Loading data...")
    all_data = load_all_data(DAYS)

    print("\n[2/3] Resampling to H1 + detecting signals...")
    tf_data = {}
    for sym, merged in all_data.items():
        resampled = resample_merged(merged, '1h')
        if len(resampled) >= 50:
            tf_data[sym] = resampled
    print(f"  H1: {len(tf_data)} tickers")

    all_sigs = detect_scored_signals(tf_data)
    print(f"  Raw signals: {len(all_sigs)}")

    sigs_per_day = compute_signals_per_day(all_sigs)
    print(f"  Signals/day: {sigs_per_day:.1f}")

    if len(all_sigs) < 5:
        print("  ❌ Too few signals")
        return

    for s in all_sigs:
        s['_time_dt'] = pd.Timestamp(s['time'])

    # ── Portfolio (noroll only) ──────────────────────────────────────────────
    print("\n[3/3] Running portfolio (noroll mode)...")
    params = dict(
        initial_capital=100000, max_dd=0.20, margin_usage=0.10,
        max_concurrent=8, total_margin_limit=0.20, stop_loss_pct=0.02,
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
        max_hold_bars=40, allow_rollover=False,
    )
    p = BarLevelPortfolio(**params)
    result = p.run(all_sigs)
    wr = compute_wr(result['trades'])
    reasons = Counter(t['exit_reason'] for t in result['trades'])
    rollover_pct = reasons.get('rollover', 0) / len(result['trades']) * 100 if result['trades'] else 0

    print(f"\n  Return: {result['total_return_pct']:.2f}%")
    print(f"  Max DD: {result['max_dd_pct']:.2f}%")
    print(f"  Calmar: {result['calmar']:.4f}")
    print(f"  Trades: {len(result['trades'])}")
    print(f"  WR: {wr:.1f}%")
    print(f"  Rollover: {rollover_pct:.0f}%")
    print(f"  Exit reasons: {dict(reasons)}")

    # ── Walk-forward ────────────────────────────────────────────────────────
    if len(result['trades']) >= 10:
        print("\n  Walk-forward...")
        n = len(all_sigs)
        n4 = n // 4
        folds = [all_sigs[:n4], all_sigs[n4:2*n4], all_sigs[2*n4:3*n4], all_sigs[3*n4:]]

        for fold_i, fs in enumerate(folds):
            if len(fs) < 5:
                print(f"    Fold {fold_i+1}: skipped ({len(fs)} signals)")
                continue
            groups = {}
            for s in fs:
                groups.setdefault(s['_time_dt'], []).append(s)
            sorted_times = sorted(groups.keys())
            p2 = BarLevelPortfolio(**params)
            r = p2._run_grouped(sorted_times, groups)
            print(f"    Fold {fold_i+1}: ret={r['total_return_pct']:.2f}% DD={r['max_dd_pct']:.2f}% Calmar={r['calmar']:.4f} trades={len(r['trades'])}")
    else:
        print(f"\n  ❌ Too few trades ({len(result['trades'])}) for WF")
        for t in result['trades']:
            print(f"    {t['ticker']} {t['direction']} PnL={t['pnl']} {t['exit_reason']}")

    # ── Ticker analysis ─────────────────────────────────────────────────────
    ticker_counts = Counter(s['ticker'] for s in all_sigs)
    print("\n  Top tickers by signals:")
    for tk, cnt in ticker_counts.most_common(15):
        print(f"    {tk}: {cnt}")

    print(f"\n⏱  {time.time()-t_start:.0f}s")

if __name__ == '__main__':
    run()