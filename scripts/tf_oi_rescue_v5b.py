#!/usr/bin/env python3
"""TF OI Rescue v5b: H1 noroll — higher capital + shorter hold."""
import sys, os, json, pickle, time
from datetime import datetime, timedelta, timezone
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import psycopg2

from trading_bot.new_strategies import detect_oi_divergence_signals
from scripts.bar_level_sim import (
    BarLevelPortfolio, TICKER_CONFIGS, TICKER_PRIORITY,
)

DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='')
ALL_TICKERS = sorted(TICKER_CONFIGS.keys())
DAYS = 365

PARAM_CFG = dict(lookback=20, extreme_window=10, horizon=12,
    bear_threshold=0.90, bull_threshold=1.10, min_gap_bars=6)

def load_all_data(days=365):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    all_data = {}
    conn = psycopg2.connect(**DB)
    try:
        for sym in ALL_TICKERS:
            cur = conn.cursor()
            cur.execute("SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
            ohlcv = [{'time': r[0], 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in cur]
            cur.close()
            if not ohlcv or len(ohlcv) < 100: continue
            cur = conn.cursor()
            cur.execute("SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time", (sym, since))
            oi_rows = [{'time': r[0], 'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]), 'yur_buy': float(r[3]), 'yur_sell': float(r[4]), 'total_oi': float(r[5])} for r in cur]
            cur.close()
            oi_map = {r['time'].strftime('%Y-%m-%d %H:%M'): r for r in oi_rows}
            merged = []
            for r in ohlcv:
                t_str = r['time'].strftime('%Y-%m-%d %H:%M')
                o = oi_map.get(t_str)
                if o is None: continue
                merged.append({'time': r['time'], 'open': r['open'], 'high': r['high'], 'low': r['low'], 'close': r['close'], 'volume': r['volume'], 'total_oi': o['total_oi'], 'fiz_buy': o['fiz_buy'], 'fiz_sell': o['fiz_sell'], 'yur_buy': o['yur_buy'], 'yur_sell': o['yur_sell'], 'symbol': sym})
            if len(merged) < 100: continue
            all_data[sym] = merged
    finally:
        conn.close()
    print(f"  Loaded {len(all_data)}/{len(ALL_TICKERS)}")
    return all_data

def resample_merged(merged, rule='1h'):
    df = pd.DataFrame([{'time': r['time'], 'open': r['open'], 'high': r['high'], 'low': r['low'], 'close': r['close'], 'volume': r['volume'], 'total_oi': r['total_oi'], 'fiz_buy': r['fiz_buy'], 'fiz_sell': r['fiz_sell'], 'yur_buy': r['yur_buy'], 'yur_sell': r['yur_sell']} for r in merged])
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'total_oi': 'last', 'fiz_buy': 'last', 'fiz_sell': 'last', 'yur_buy': 'last', 'yur_sell': 'last'}
    resampled = df.resample(rule).agg(agg).dropna(subset=['close'])
    sym = merged[0].get('symbol', '?')
    out = []
    for idx, row in resampled.iterrows():
        out.append({'time': idx, 'open': float(row['open']), 'high': float(row['high']), 'low': float(row['low']), 'close': float(row['close']), 'volume': float(row['volume']), 'total_oi': float(row['total_oi']), 'fiz_buy': float(row['fiz_buy']), 'fiz_sell': float(row['fiz_sell']), 'yur_buy': float(row['yur_buy']), 'yur_sell': float(row['yur_sell']), 'symbol': sym})
    return out

def detect_signals(tf_data):
    all_sigs = []
    for sym, resampled in tf_data.items():
        if len(resampled) < 50: continue
        sigs = detect_oi_divergence_signals(resampled, PARAM_CFG)
        for s in sigs:
            idx = s.get('idx')
            if idx is None or idx >= len(resampled): continue
            s['ticker'] = sym
            s['time'] = str(resampled[idx]['time'])
            s['score'] = 0.5
            s['atr_pct'] = 0.01
            s['adx_value'] = 20
            s['_param_horizon'] = PARAM_CFG['horizon']
            all_sigs.append(s)
    return all_sigs

# ── Multi-config test ────────────────────────────────────────────────────────

def run_config(label, capital, margin_usage, max_conc, total_margin_limit, max_hold_bars, stop_loss_pct, horizon=None):
    params = dict(initial_capital=capital, max_dd=0.20, margin_usage=margin_usage,
        max_concurrent=max_conc, total_margin_limit=total_margin_limit, stop_loss_pct=stop_loss_pct,
        use_score_sizing=True, use_score_eviction=False, atr_stop_mult=2.0,
        use_score_decay=True, use_mtm=True, use_trailing=True, trailing_mult=3.0,
        max_hold_bars=max_hold_bars, allow_rollover=False)
    
    # If horizon override needed, modify param config
    cfg = dict(PARAM_CFG)
    if horizon:
        cfg['horizon'] = horizon
        # Need to regenerate signals
        return None  # skip, handle separately
    
    p = BarLevelPortfolio(**params)
    result = p.run(signals)
    wr = sum(1 for t in result['trades'] if t['pnl'] > 0) / len(result['trades']) * 100 if result['trades'] else 0
    reasons = Counter(t['exit_reason'] for t in result['trades'])
    rollover_pct = reasons.get('rollover', 0) / len(result['trades']) * 100 if result['trades'] else 0
    print(f"  {label:<35} ret={result['total_return_pct']:>7.2f}% DD={result['max_dd_pct']:>6.2f}% Calmar={result['calmar']:.4f} T={len(result['trades']):>2} WR={wr:.0f}% R={rollover_pct:.0f}%")
    return result

# ── Main ─────────────────────────────────────────────────────────────────────

t_start = time.time()
print(f"  TF OI Rescue v5b — {datetime.now():%Y-%m-%d}")
print(f"  Params: {PARAM_CFG}")
print()

print("[1/3] Loading data...")
all_data = load_all_data(DAYS)

print("\n[2/3] Resampling + signals...")
tf_data = {}
for sym, merged in all_data.items():
    resampled = resample_merged(merged, '1h')
    if len(resampled) >= 50:
        tf_data[sym] = resampled
print(f"  H1: {len(tf_data)} tickers")

signals12 = detect_signals(tf_data)
for s in signals12: s['_time_dt'] = pd.Timestamp(s['time'])
print(f"  H1 signals (horizon=12): {len(signals12)} ({len(signals12)/365:.1f}/day)")

# Generate signals with horizon=6
cfg6 = dict(PARAM_CFG)
cfg6['horizon'] = 6
signals6 = []
for sym, resampled in tf_data.items():
    if len(resampled) < 50: continue
    sigs = detect_oi_divergence_signals(resampled, cfg6)
    for s in sigs:
        idx = s.get('idx')
        if idx is None or idx >= len(resampled): continue
        s['ticker'] = sym
        s['time'] = str(resampled[idx]['time'])
        s['score'] = 0.5
        s['atr_pct'] = 0.01
        s['adx_value'] = 20
        s['_param_horizon'] = 6
        signals6.append(s)
for s in signals6: s['_time_dt'] = pd.Timestamp(s['time'])
print(f"  H1 signals (horizon=6):  {len(signals6)} ({len(signals6)/365:.1f}/day)")

print("\n[3/3] Portfolio tests...")
print()

# horizon=12 variants
signals = signals12
run_config("H1 100K/10%/8/20%/2%SL/40h", 100000, 0.10, 8, 0.20, 40, 0.02)
run_config("H1 200K/10%/16/20%/2%SL/40h", 200000, 0.10, 16, 0.20, 40, 0.02)
run_config("H1 200K/15%/16/25%/2%SL/40h", 200000, 0.15, 16, 0.25, 40, 0.02)
run_config("H1 300K/15%/24/25%/2%SL/40h", 300000, 0.15, 24, 0.25, 40, 0.02)
run_config("H1 500K/20%/30/30%/2%SL/40h", 500000, 0.20, 30, 0.30, 40, 0.02)
run_config("H1 200K/10%/16/20%/1%SL/40h", 200000, 0.10, 16, 0.20, 0.01, 40)
run_config("H1 100K/10%/20/20%/noSL/40h", 100000, 0.10, 20, 0.20, 0, 40)
run_config("H1 100K/15%/20/25%/2%SL/80h", 100000, 0.15, 20, 0.25, 0.02, 80)

# horizon=6 variants
signals = signals6
print()
run_config("H1 h=6 100K/10%/8/20%/2%SL/40h", 100000, 0.10, 8, 0.20, 40, 0.02)
run_config("H1 h=6 200K/15%/16/25%/2%SL/40h", 200000, 0.15, 16, 0.25, 40, 0.02)
run_config("H1 h=6 300K/15%/24/25%/2%SL/40h", 300000, 0.15, 24, 0.25, 40, 0.02)

print(f"\n⏱ {time.time()-t_start:.0f}s")