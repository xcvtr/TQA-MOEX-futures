#!/usr/bin/env python3
"""Robust signal collector with per-ticker caching and DB timeout handling."""
import json, os, sys, time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Set DB connect timeout for robustness
import psycopg2
from trading_bot import new_strategies
new_strategies.DB['connect_timeout'] = 10

CACHE_PATH = os.path.join(PROJECT_ROOT, '.signals_cache.json')
HISTORY_DAYS = 365
SCORE_THRESHOLD = 0.3

from trading_bot.new_strategies import load_ohlcv, load_oi, merge_ohlcv_oi, detect_oi_divergence_signals_limit
from trading_bot.strategy_cascade import compute_quality_score
from trading_bot.filters import calc_atr, calc_adx

QUALIFIED_TICKERS = [
    'AF','AU','BR','CC','CE','CH','CNYRUBF','CR','DX','ED',
    'EURRUBF','FF','GD','GK','GL','GLDRUBF','GZ','HS','HY',
    'IMOEXF','KC','MC','ME','MG','MN','MX','NA','NM','PD',
    'RB','RI','RL','RN','SBERF','SE','SF','SN','SP','SR',
    'SS','SV','Si','TN','TT','UC','VI','W4',
]

print(f"Signal collector — {len(QUALIFIED_TICKERS)} tickers, {HISTORY_DAYS}d, score>={SCORE_THRESHOLD}", flush=True)
print(f"Cache: {CACHE_PATH}", flush=True)
t_start = time.time()

all_signals = []
errors = []

for i, sym in enumerate(QUALIFIED_TICKERS):
    t0 = time.time()
    print(f"  [{i+1}/{len(QUALIFIED_TICKERS)}] {sym}...", end=" ", flush=True)
    try:
        ohlcv = load_ohlcv(sym, HISTORY_DAYS)
        if not ohlcv or len(ohlcv) < 100:
            print(f"skip — {len(ohlcv) if ohlcv else 0} OHLCV", flush=True)
            continue
        oi = load_oi(sym, HISTORY_DAYS)
        if not oi:
            print(f"skip — no OI", flush=True)
            continue
        merged = merge_ohlcv_oi(ohlcv, oi)
        if not merged or len(merged) < 100:
            print(f"skip — {len(merged) if merged else 0} merged", flush=True)
            continue

        sigs = detect_oi_divergence_signals_limit(merged, {'horizon': 12})
        if not sigs:
            print(f"0 signals", flush=True)
            continue

        # Precompute ATR/ADX
        closes = [r['close'] for r in merged]
        highs = [r['high'] for r in merged]
        lows = [r['low'] for r in merged]
        try:
            atr_vals = calc_atr(highs, lows, closes, 14)
            adx_vals = calc_adx(closes, 14)
        except:
            atr_vals = []
            adx_vals = []

        scored = passed = 0
        for s in sigs:
            idx = s.get('idx')
            if idx is None or idx >= len(merged):
                continue
            quality = compute_quality_score(merged, idx)
            s['score'] = quality['total']
            s['score_components'] = quality['components']
            s['ticker'] = sym
            if atr_vals and idx < len(atr_vals) and closes[idx] > 0:
                s['atr_value'] = atr_vals[idx]
                s['atr_pct'] = atr_vals[idx] / closes[idx]
            if adx_vals and idx < len(adx_vals):
                s['adx_value'] = adx_vals[idx]
            scored += 1
            if quality['total'] >= SCORE_THRESHOLD:
                passed += 1
                all_signals.append(s)

        dt = time.time() - t0
        print(f"{len(sigs)} sigs, {passed}/{scored} pass, {dt:.1f}s", flush=True)

    except Exception as e:
        errors.append(f"{sym}: {e}")
        print(f"ERROR: {e}", flush=True)

    # Save partial progress every 5 tickers
    if (i + 1) % 5 == 0:
        try:
            minimal = [{k: v for k, v in s.items() if k != 'score_components'} for s in all_signals]
            with open(CACHE_PATH, 'w') as f:
                json.dump(minimal, f)
        except:
            pass

# Sort all
all_signals.sort(key=lambda s: str(s.get('time', '')))
print(f"\nTotal: {len(all_signals)} signals from {len(QUALIFIED_TICKERS)-len(errors)} tickers", flush=True)
print(f"Time: {time.time()-t_start:.0f}s", flush=True)
if errors:
    print(f"Errors: {len(errors)}", flush=True)
    for e in errors[:5]:
        print(f"  - {e}", flush=True)

# Save final cache
try:
    minimal = [{k: v for k, v in s.items() if k != 'score_components'} for s in all_signals]
    with open(CACHE_PATH, 'w') as f:
        json.dump(minimal, f)
    print(f"Cache saved: {CACHE_PATH} ({len(minimal)} signals)", flush=True)
except Exception as e:
    print(f"Cache save failed: {e}", flush=True)

print("Done!", flush=True)
