#!/usr/bin/env python3
"""
Полный аудит всех стратегий через честную bar-level симуляцию (OHLCV close, MTM).
Каждая стратегия: собрать сигналы по 47 тикерам → BarLevelPortfolio.run().

Usage:
    python -m scripts.audit_strategies
"""

import json, os, sys, time
from collections import Counter
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot.new_strategies import (
    load_ohlcv as _load_ohlcv, load_oi as _load_oi, merge_ohlcv_oi,
    detect_otc_signals, detect_retail_trap_signals,
    detect_vwap_signals, detect_oi_divergence_signals,
    detect_oi_divergence_signals_limit,
)
from trading_bot.filters import calc_atr, calc_adx
from scripts.bar_level_sim import BarLevelPortfolio

# ── Config ──
QUALIFIED_TICKERS = [
    'AF','AU','BR','CC','CE','CH','CNYRUBF','CR','DX','ED',
    'EURRUBF','FF','GD','GK','GL','GLDRUBF','GZ','HS','HY',
    'IMOEXF','KC','MC','ME','MG','MN','MX','NA','NM','PD',
    'RB','RI','RL','RN','SBERF','SE','SF','SN','SP','SR',
    'SS','SV','Si','TN','TT','UC','VI','W4',
]
HISTORY_DAYS = 365
SCORE_THRESHOLD = 0.3

DEFAULT_PORTFOLIO_PARAMS = dict(
    initial_capital=100000,
    max_dd=0.10,
    margin_usage=0.10,
    max_concurrent=5,
    total_margin_limit=0.15,
    stop_loss_pct=0.01,
    use_score_sizing=True,
    use_score_eviction=True,
    atr_stop_mult=2.0,
    use_score_decay=True,
    max_hold_bars=40,
    use_mtm=True,
    use_trailing=False,
    trailing_mult=3.0,
)

# ── Shared data cache (avoid redundant DB loads across strategies) ──
_data_cache = {}

def _get_data(sym, with_oi=True):
    key = (sym, with_oi)
    if key in _data_cache:
        return _data_cache[key]
    ohlcv = _load_ohlcv(sym, HISTORY_DAYS)
    if not ohlcv or len(ohlcv) < 100:
        _data_cache[key] = None
        return None
    if with_oi:
        oi = _load_oi(sym, HISTORY_DAYS)
        if not oi:
            _data_cache[key] = None
            return None
        data = merge_ohlcv_oi(ohlcv, oi)
    else:
        data = ohlcv
    if not data or len(data) < 100:
        _data_cache[key] = None
        return None
    _data_cache[key] = data
    return data


def _enrich_signals(sigs, data, sym):
    n = len(data)
    closes = [r['close'] for r in data]
    highs = [r['high'] for r in data]
    lows = [r['low'] for r in data]

    if n > 14:
        atr_vals = calc_atr(highs, lows, closes, 14)
        adx_vals = calc_adx(closes, 14)
    else:
        atr_vals = [0.0] * n
        adx_vals = [0.0] * n

    # Precompute volume SMA (20 bars) and vol_ratio for fast scoring
    vol_sma = [0.0] * n
    volumes = [r['volume'] for r in data]
    for i in range(20, n):
        vol_sma[i] = sum(volumes[i-20:i]) / 20.0

    # Precompute whale z-scores if OI data available
    buy_z, sell_z = None, None
    if 'yur_buy' in data[0] and 'yur_sell' in data[0]:
        yur_buy = [r['yur_buy'] for r in data]
        yur_sell = [r['yur_sell'] for r in data]
        buy_z = _fast_zs(yur_buy, 20, n)
        sell_z = _fast_zs(yur_sell, 20, n)

    enriched = []
    for s in sigs:
        idx = s.get('idx')
        if idx is None or idx >= n:
            continue
        # Fast quality score without per-signal ADX/ATR recomputation
        score = _fast_quality_score(
            idx, adx_vals, atr_vals, vol_sma, volumes,
            closes, buy_z, sell_z, data
        )
        s['score'] = score
        s['ticker'] = sym
        if atr_vals and idx < len(atr_vals) and closes[idx] > 0:
            s['atr_value'] = atr_vals[idx]
            s['atr_pct'] = atr_vals[idx] / closes[idx]
        if adx_vals and idx < len(adx_vals):
            s['adx_value'] = adx_vals[idx]
        if score >= SCORE_THRESHOLD:
            enriched.append(s)
    return enriched


def _fast_zs(vals, w, n):
    """Precompute z-scores for all indices, NO look-ahead."""
    out = [0.0] * n
    for i in range(w, n):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x-mu)**2 for x in chunk) / w
        sd = var**0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def _fast_quality_score(idx, adx_vals, atr_vals, vol_sma, volumes,
                         closes, buy_z, sell_z, data):
    """
    Fast quality score (0..1) computed from pre-allocated arrays.
    Weights: adx=0.25, volume=0.20, whale=0.25, atr=0.15, hvn_proxy=0.15
    """
    score = 0.0

    # 1. ADX (0..1): 40+ = max
    if idx < len(adx_vals):
        adx_score = min(adx_vals[idx] / 40.0, 1.0)
    else:
        adx_score = 0.0
    score += adx_score * 0.25

    # 2. Volume ratio (0..1): 2x avg = max
    if idx < len(vol_sma) and vol_sma[idx] > 0:
        vol_ratio = volumes[idx] / vol_sma[idx]
        vol_score = min(vol_ratio / 2.0, 1.0)
    else:
        vol_score = 0.0
    score += vol_score * 0.20

    # 3. Whale z-score (0..1): 3σ = max
    if buy_z and sell_z and idx < len(buy_z):
        max_z = max(abs(buy_z[idx]), abs(sell_z[idx]))
        whale_score = min(max_z / 3.0, 1.0)
    else:
        whale_score = 0.0
    score += whale_score * 0.25

    # 4. ATR calmness (0..1): ATR < 1% = calm (max)
    if idx < len(atr_vals) and closes[idx] > 0:
        atr_ratio = atr_vals[idx] / closes[idx]
        atr_score = max(1.0 - min(atr_ratio / 0.03, 1.0), 0.0)
    else:
        atr_score = 0.0
    score += atr_score * 0.15

    # 5. HVN proxy (0..1): simple proxy using distance from recent SMA
    if idx > 20:
        sma20 = sum(closes[idx-20:idx]) / 20.0
        if sma20 > 0:
            hvn_dist = abs(closes[idx] - sma20) / sma20
            hvn_score = max(1.0 - min(hvn_dist / 0.03, 1.0), 0.0)
        else:
            hvn_score = 0.0
    else:
        hvn_score = 0.0
    score += hvn_score * 0.15

    return round(score, 4)


# ── Signal Collectors ──

def _collect(name, strategy_fn, config, needs_oi=True):
    """Generic signal collector: load all tickers, run strategy_fn, enrich, filter."""
    all_sigs = []
    for sym in QUALIFIED_TICKERS:
        data = _get_data(sym, with_oi=needs_oi)
        if data is None:
            continue
        sigs = strategy_fn(data, config=config)
        all_sigs.extend(_enrich_signals(sigs, data, sym))
    all_sigs.sort(key=lambda s: str(s.get('time', '')))
    print(f"  → {name}: {len(all_sigs)} signals (score>={SCORE_THRESHOLD})")
    return all_sigs


def collect_otc(config=None):
    cfg = {'oi_z_thresh': 0.5, 'price_z_thresh': 0.5, 'horizon': 12, **(config or {})}
    return _collect('OTC', detect_otc_signals, cfg, needs_oi=True)


def collect_retail_trap(config=None):
    cfg = {'fiz_z_thresh': 1.5, 'horizon': 12, **(config or {})}
    return _collect('Retail Trap', detect_retail_trap_signals, cfg, needs_oi=True)


def collect_vwap(config=None):
    cfg = {'dev_thresh': 2.0, 'horizon': 12, 'vwap_window': 20, 'atr_period': 14, **(config or {})}
    return _collect('VWAP', detect_vwap_signals, cfg, needs_oi=False)


def collect_oi_div_market(config=None):
    cfg = {'lookback': 20, 'horizon': 12, 'extreme_window': 10,
           'bear_threshold': 0.95, 'bull_threshold': 1.05, **(config or {})}
    return _collect('OI Div Market', detect_oi_divergence_signals, cfg, needs_oi=True)


def collect_oi_div_limit(config=None):
    cfg = {'lookback': 20, 'horizon': 12, 'extreme_window': 10,
           'bear_threshold': 0.95, 'bull_threshold': 1.05,
           'limit_lookback': 5, **(config or {})}
    return _collect('OI Div Limit', detect_oi_divergence_signals_limit, cfg, needs_oi=True)


# ── TRIZ: Mean Reversion v2 ──

def detect_mean_reversion_v2(ohlcv, config=None):
    default = {'z_entry': 2.0, 'horizon': 12, 'ma_window': 20}
    cfg = {**default, **(config or {})}
    n = len(ohlcv)
    if n < 50:
        return []
    closes = [r['close'] for r in ohlcv]
    w = cfg['ma_window']
    horizon = cfg['horizon']
    z_entry = cfg['z_entry']

    sma = [0.0] * n
    for i in range(w, n):
        sma[i] = sum(closes[i - w:i]) / w

    z_score = [0.0] * n
    for i in range(w, n):
        chunk = closes[i - w:i]
        mu = sma[i]
        var = sum((x - mu) ** 2 for x in chunk) / w
        sd = var ** 0.5
        z_score[i] = (closes[i] - mu) / sd if sd > 0 else 0.0

    signals = []
    min_idx = w + 5

    for i in range(min_idx, n):
        if i + 1 >= n or i + horizon >= n:
            continue
        if z_score[i] > z_entry:
            direction = 'SHORT'
        elif z_score[i] < -z_entry:
            direction = 'LONG'
        else:
            continue
        entry = ohlcv[i + 1]['open']
        if entry <= 0:
            continue
        exit_price = ohlcv[i + horizon]['close']
        ret = ((exit_price - entry) / entry * 100) if direction == 'LONG' else ((entry - exit_price) / entry * 100)
        signals.append({
            'ticker': ohlcv[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': ohlcv[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'mean_reversion_v2', 'idx': i, 'z_score': round(z_score[i], 4),
        })
    return signals


def collect_mean_reversion_v2(config=None):
    cfg = {'z_entry': 2.0, 'horizon': 12, 'ma_window': 20, **(config or {})}
    return _collect('Mean Reversion v2', detect_mean_reversion_v2, cfg, needs_oi=False)


# ── TRIZ: Volatility Breakout ──

def detect_volatility_breakout(ohlcv, config=None):
    default = {'channel_mult': 2.0, 'horizon': 12, 'sma_window': 20, 'atr_period': 14}
    cfg = {**default, **(config or {})}
    n = len(ohlcv)
    if n < 50:
        return []
    closes = [r['close'] for r in ohlcv]
    highs = [r['high'] for r in ohlcv]
    lows = [r['low'] for r in ohlcv]

    sw = cfg['sma_window']
    atr_p = cfg['atr_period']
    ch_mult = cfg['channel_mult']
    horizon = cfg['horizon']

    sma = [0.0] * n
    for i in range(sw, n):
        sma[i] = sum(closes[i - sw:i]) / sw
    atr_vals = calc_atr(highs, lows, closes, atr_p)

    signals = []
    min_idx = max(sw, atr_p) + 5
    for i in range(min_idx, n):
        if i + 1 >= n or i + horizon >= n:
            continue
        if sma[i] <= 0 or atr_vals[i] <= 0:
            continue
        upper = sma[i] + ch_mult * atr_vals[i]
        lower = sma[i] - ch_mult * atr_vals[i]
        if closes[i] > upper:
            direction = 'SHORT'
        elif closes[i] < lower:
            direction = 'LONG'
        else:
            continue
        entry = ohlcv[i + 1]['open']
        if entry <= 0:
            continue
        exit_price = ohlcv[i + horizon]['close']
        ret = ((exit_price - entry) / entry * 100) if direction == 'LONG' else ((entry - exit_price) / entry * 100)
        signals.append({
            'ticker': ohlcv[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': ohlcv[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'volatility_breakout', 'idx': i,
            'channel_upper': round(upper, 4), 'channel_lower': round(lower, 4),
        })
    return signals


def collect_volatility_breakout(config=None):
    cfg = {'channel_mult': 2.0, 'horizon': 12, 'sma_window': 20, 'atr_period': 14, **(config or {})}
    return _collect('Volatility Breakout', detect_volatility_breakout, cfg, needs_oi=False)


# ── Runners ──

def _prepare_signals(signals):
    """Add _time_dt and ensure all required fields exist for BarLevelPortfolio."""
    import pandas as pd
    out = []
    for s in signals:
        s = dict(s)
        s['_time_dt'] = pd.Timestamp(s.get('time', ''))
        out.append(s)
    return out


def run_portfolio(name, signals, **overrides):
    params = dict(DEFAULT_PORTFOLIO_PARAMS, **overrides)
    sigs = _prepare_signals(signals)
    p = BarLevelPortfolio(**params)
    result = p.run(sigs)
    reasons = dict(Counter(t['exit_reason'] for t in result['trades']))
    print(f"  → {name}: ret={result['total_return_pct']:.2f}%  DD={result['max_dd_pct']:.2f}%  "
          f"Calmar={result['calmar']:.4f}  trades={len(result['trades'])}  "
          f"exit_reasons={reasons}")
    return result


# ── Main ──

def _cache_name(name):
    """Normalize name to valid filename for .signals_{name}.json"""
    n = name.lower().replace(' ', '_').replace('(', '').replace(')', '')
    n = n.replace('wide_stops', 'wide_stops')
    return n


def main():
    t_start = time.time()
    results = {}

    def phase(description):
        print(f"\n{'='*70}")
        print(f"  {description}")
        print(f"{'='*70}")

    # ═══ Phase 1: Existing Strategies ═══
    phase("PHASE 1: EXISTING STRATEGIES (honest bar-level simulation)")

    existing = [
        ("OTC", collect_otc),
        ("Retail Trap", collect_retail_trap),
        ("VWAP", collect_vwap),
        ("OI Div Market", collect_oi_div_market),
        ("OI Div Limit", collect_oi_div_limit),
    ]

    for name, collector in existing:
        t0 = time.time()
        print(f"\n--- {name} ---")
        try:
            sigs = collector()
            if not sigs:
                print(f"  WARNING: no signals for {name}")
                results[name] = None
                continue
            path = f'.signals_{_cache_name(name)}.json'
            with open(path, 'w') as f:
                json.dump(sigs, f)
            print(f"  Cached {len(sigs)} → {path}")
            results[name] = run_portfolio(name, sigs)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            results[name] = None
        print(f"  Time: {time.time()-t0:.0f}s")

    # ═══ Phase 2: TRIZ Strategies ═══
    phase("PHASE 2: TRIZ-DESIGNED STRATEGIES")

    triz = [
        ("Mean Reversion v2", collect_mean_reversion_v2),
        ("Volatility Breakout", collect_volatility_breakout),
    ]

    for name, collector in triz:
        t0 = time.time()
        print(f"\n--- {name} ---")
        try:
            sigs = collector()
            if not sigs:
                print(f"  WARNING: no signals for {name}")
                results[name] = None
                continue
            path = f'.signals_{_cache_name(name)}.json'
            with open(path, 'w') as f:
                json.dump(sigs, f)
            print(f"  Cached {len(sigs)} → {path}")
            results[name] = run_portfolio(name, sigs)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            results[name] = None
        print(f"  Time: {time.time()-t0:.0f}s")

    # ═══ Phase 2B: Wide Stops ═══
    phase("PHASE 2B: WIDE STOPS (sl=5%, max_hold=80)")

    for base_name in ['OI Div Limit', 'OI Div Market']:
        wide_name = f"{base_name} (wide stops)"
        print(f"\n--- {wide_name} ---")
        try:
            path = f'.signals_{_cache_name(base_name)}.json'
            if not os.path.exists(path):
                print(f"  SKIP: no cache {path}")
                results[wide_name] = None
                continue
            with open(path) as f:
                sigs = json.load(f)
            print(f"  Loaded {len(sigs)} signals")
            results[wide_name] = run_portfolio(wide_name, sigs,
                                               stop_loss_pct=0.05, max_hold_bars=80,
                                               total_margin_limit=0.20, max_dd=0.20)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            results[wide_name] = None

    # ═══ Phase 2C: Ensemble ═══
    phase("PHASE 2C: ENSEMBLE")

    ranked = [(n, r) for n, r in results.items()
              if r is not None and r['calmar'] > 0]
    ranked.sort(key=lambda x: x[1]['calmar'], reverse=True)
    print(f"\n  Strategies by Calmar:")
    for n, r in ranked:
        print(f"    {n:<35} Calmar={r['calmar']:.4f}  ret={r['total_return_pct']:.2f}%  DD={r['max_dd_pct']:.2f}%")

    # Deduplicate: skip wide stops variants that use same signal sets as their base
    unique_top = []
    seen_sets = set()
    for n, r in ranked:
        base = n.replace(' (wide stops)', '')
        if base not in seen_sets:
            seen_sets.add(base)
            unique_top.append((n, r))
        if len(unique_top) >= 3:
            break

    if len(unique_top) >= 2:
        print(f"\n  Building ensemble from TOP-{len(unique_top)}...")
        ensemble_capital = DEFAULT_PORTFOLIO_PARAMS['initial_capital']
        each_cap = ensemble_capital / len(unique_top)
        sub_results = []

        for name, _ in unique_top:
            path = f'.signals_{_cache_name(name)}.json'
            if not os.path.exists(path):
                # Try alternate naming (remove qualifiers)
                alt_name = name.replace(' (wide stops)', '').replace(' (market)', '')
                path = f'.signals_{_cache_name(alt_name)}.json'
            if not os.path.exists(path):
                print(f"    WARNING: cache not found for {name} (tried {path})")
                continue
            with open(path) as f:
                sigs = json.load(f)
            sigs = _prepare_signals(sigs)
            params = dict(DEFAULT_PORTFOLIO_PARAMS, initial_capital=each_cap)
            p = BarLevelPortfolio(**params)
            r = p.run(sigs)
            sub_results.append(r)
            print(f"    Sub: {name:<35} ret={r['total_return_pct']:>6.2f}% DD={r['max_dd_pct']:>5.2f}% trades={len(r['trades'])}")

        if sub_results:
            combined_capital = sum(r['final_capital'] for r in sub_results)
            total_ret = (combined_capital / ensemble_capital - 1) * 100
            combined_dd = max(r['max_dd_pct'] for r in sub_results)
            combined_calmar = total_ret / (combined_dd * 100) if combined_dd > 0 else 0
            combined_trades = sum(len(r['trades']) for r in sub_results)
            results['Ensemble (top)'] = {
                'total_return_pct': total_ret,
                'max_dd_pct': combined_dd,
                'calmar': combined_calmar,
                'trades': [],
                'final_capital': combined_capital,
                'equity_curve': [],
            }
            print(f"\n  → Ensemble result:")
            print(f"  ret={total_ret:.2f}%  DD={combined_dd:.2f}%  Calmar={combined_calmar:.4f}  trades={combined_trades}")

    # ═══ Final Report ═══
    phase("FINAL REPORT")

    print(f"\n| {'Strategy':<35} | {'Return':>7} | {'DD':>6} | {'Calmar':>7} | {'Trades':>5} | Exit Reasons")
    print(f"|{'-'*37}|{'-'*9}|{'-'*8}|{'-'*9}|{'-'*7}|{'-'*50}|")
    for name, r in results.items():
        if r is None:
            print(f"| {name:<35} |  ERROR  |        |         |       |")
        else:
            reasons = dict(Counter(t['exit_reason'] for t in r['trades']))
            print(f"| {name:<35} | {r['total_return_pct']:>6.2f}% | {r['max_dd_pct']:>5.2f}% | {r['calmar']:>7.4f} | {len(r['trades']):>5} | {reasons!s:<50}")

    valid = [(n, r) for n, r in results.items()
             if r is not None and r['total_return_pct'] > 0 and r['max_dd_pct'] <= 20]
    valid.sort(key=lambda x: x[1]['total_return_pct'], reverse=True)

    print(f"\n{'='*70}")
    if valid:
        for n, r in valid:
            print(f"  ✓ {n:<35} ret={r['total_return_pct']:>6.2f}% DD={r['max_dd_pct']:>5.2f}% Calmar={r['calmar']:.4f}")
        best = valid[0]
        print(f"\n  BEST: {best[0]} — ret={best[1]['total_return_pct']:.2f}%  DD={best[1]['max_dd_pct']:.2f}%  Calmar={best[1]['calmar']:.4f}")
    else:
        print(f"\n  No strategy meets return > 0 with DD ≤ 20%")
        print(f"  Target: 80%+ annual return with DD ≤ 20%")

    print(f"\nTotal time: {time.time()-t_start:.0f}s")
    return results


if __name__ == '__main__':
    main()
