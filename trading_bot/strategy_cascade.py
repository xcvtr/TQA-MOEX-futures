"""
Strategy Cascade — 5 independent filters on top of any base strategy.

Each filter: function(data, config) -> bool  (True = signal passes)
Combined in detect_cascade_signals() which requires ALL filters to pass.
"""

from typing import List, Dict, Optional, Callable
from .filters import calc_adx, calc_atr
from .strategy_profile import _find_hvn_levels


def _zs(vals, w=20):
    """Rolling z-score, NO look-ahead."""
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x-mu)**2 for x in chunk) / w
        sd = var**0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def adx_filter(data: List[Dict], idx: int, threshold: float = 25, period: int = 14) -> bool:
    """
    ADX(14) on close > threshold.
    True = trend strong enough for signal.
    """
    closes = [r['close'] for r in data]
    adx = calc_adx(closes, period)
    if idx >= len(adx):
        return False
    return adx[idx] > threshold


def volume_filter(data: List[Dict], idx: int, vol_mult: float = 1.5, window: int = 20) -> bool:
    """
    Volume > vol_mult * SMA(volume, window).
    True = volume confirms the move.
    """
    if idx < window:
        return False
    volumes = [r['volume'] for r in data]
    avg_vol = sum(volumes[idx-window:idx]) / window
    if avg_vol <= 0:
        return False
    return volumes[idx] > vol_mult * avg_vol


def whale_filter(data: List[Dict], idx: int, z_thresh: float = 1.5) -> bool:
    """
    yur_buy or yur_sell z-score > z_thresh.
    True = institutions are also moving in this direction.
    Uses the SAME data dict with yur_buy/yur_sell fields (merged OHLCV+OI).
    """
    if 'yur_buy' not in data[0] or 'yur_sell' not in data[0]:
        return False
    yur_buy = [r['yur_buy'] for r in data]
    yur_sell = [r['yur_sell'] for r in data]
    buy_z = _zs(yur_buy, 20)
    sell_z = _zs(yur_sell, 20)
    if idx >= len(buy_z):
        return False
    return abs(buy_z[idx]) > z_thresh or abs(sell_z[idx]) > z_thresh


def hvn_filter(data: List[Dict], idx: int, lookback: int = 20, vol_mult: float = 2.0,
               n_buckets: int = 10, touch_pct: float = 0.01) -> bool:
    """
    Price near HVN (High Volume Node).
    True = price is at a support/resistance level.
    Builds volume profile on data[:idx+1] (past data only).
    """
    if idx < lookback + 5:
        return False
    segment = data[max(0, idx - lookback):idx + 1]
    hvn_level, _ = _find_hvn_levels(segment, lookback=lookback, vol_mult=vol_mult, n_buckets=n_buckets)
    if hvn_level is None:
        return False
    close = data[idx]['close']
    hvn_diff = abs(close - hvn_level) / max(hvn_level, 1)
    return hvn_diff <= touch_pct


def atr_filter(data: List[Dict], idx: int, max_atr_pct: float = 0.02, period: int = 14) -> bool:
    """
    ATR(14) < max_atr_pct * price.
    True = volatility is not extreme (avoids wild moves).
    """
    if idx < period:
        return False
    highs = [r['high'] for r in data]
    lows = [r['low'] for r in data]
    closes = [r['close'] for r in data]
    atr = calc_atr(highs, lows, closes, period)
    if idx >= len(atr):
        return False
    price = closes[idx]
    if price <= 0:
        return False
    return atr[idx] < max_atr_pct * price


def apply_filters(data: List[Dict], idx: int, filters_config: Dict[str, bool],
                  filter_params: Optional[Dict] = None) -> Dict[str, bool]:
    """
    Apply all enabled filters at index idx.
    Returns dict of {filter_name: passed_bool}.
    """
    fp = filter_params or {}
    results = {}

    if filters_config.get('adx', True):
        results['adx'] = adx_filter(data, idx, threshold=fp.get('adx_threshold', 25))

    if filters_config.get('volume', True):
        results['volume'] = volume_filter(data, idx, vol_mult=fp.get('vol_mult', 1.5))

    if filters_config.get('whale', True):
        results['whale'] = whale_filter(data, idx, z_thresh=fp.get('whale_z_thresh', 1.5))

    if filters_config.get('hvn', True):
        results['hvn'] = hvn_filter(data, idx, lookback=fp.get('hvn_lookback', 20),
                                     vol_mult=fp.get('hvn_vol_mult', 2.0))

    if filters_config.get('atr', True):
        results['atr'] = atr_filter(data, idx, max_atr_pct=fp.get('max_atr_pct', 0.02))

    return results


def compute_quality_score(data: List[Dict], idx: int,
                         params: Optional[Dict] = None) -> Dict:
    """Compute continuous quality score (0..1) for a signal at index idx.
    
    TRIZ Principle 6: Universality — one score instead of binary filters.
    Each component is normalized to 0..1 and weighted.
    """
    p = params or {}
    
    # Default weights — sum to 1.0
    weights = {
        'adx': p.get('w_adx', 0.25),
        'volume': p.get('w_volume', 0.20),
        'whale': p.get('w_whale', 0.25),
        'hvn': p.get('w_hvn', 0.15),
        'atr': p.get('w_atr', 0.15),
    }
    
    scores = {}
    
    # 1. ADX strength (0..1) — 40+ = max confidence
    if idx > 14:
        closes = [r['close'] for r in data[:idx+1]]
        if len(closes) > 14:
            adx = calc_adx(closes, 14)
            if idx < len(adx):
                scores['adx'] = min(adx[idx] / 40.0, 1.0)
    
    if 'adx' not in scores:
        scores['adx'] = 0.0
    
    # 2. Volume ratio (0..1) — 2× avg = max
    if idx > 20:
        volumes = [r['volume'] for r in data]
        avg_vol = sum(volumes[idx-20:idx]) / 20.0
        if avg_vol > 0:
            vol_ratio = volumes[idx] / avg_vol
            scores['volume'] = min(vol_ratio / 2.0, 1.0)
    
    if 'volume' not in scores:
        scores['volume'] = 0.0
    
    # 3. Whale z-score strength (0..1) — 3σ = max
    if 'yur_buy' in data[0] and 'yur_sell' in data[0]:
        yur_buy = [r['yur_buy'] for r in data[:idx+1]]
        yur_sell = [r['yur_sell'] for r in data[:idx+1]]
        buy_z = _zs(yur_buy, 20)[-1] if len(yur_buy) > 20 else 0
        sell_z = _zs(yur_sell, 20)[-1] if len(yur_sell) > 20 else 0
        max_z = max(abs(buy_z), abs(sell_z))
        scores['whale'] = min(max_z / 3.0, 1.0)
    else:
        scores['whale'] = 0.0
    
    # 4. HVN proximity (0..1) — closer = better
    if idx > 25:
        segment = data[max(0, idx - 20):idx + 1]
        hvn_level, _ = _find_hvn_levels(segment, lookback=20, vol_mult=2.0, n_buckets=10)
        if hvn_level and hvn_level > 0:
            close = data[idx]['close']
            hvn_dist = abs(close - hvn_level) / hvn_level
            scores['hvn'] = max(1.0 - min(hvn_dist / 0.03, 1.0), 0.0)
    
    if 'hvn' not in scores:
        scores['hvn'] = 0.0
    
    # 5. ATR calmness (0..1) — ATR < 1% = calm (max score)
    if idx > 14:
        highs = [r['high'] for r in data[:idx+1]]
        lows = [r['low'] for r in data[:idx+1]]
        closes = [r['close'] for r in data[:idx+1]]
        if len(highs) > 14:
            atr = calc_atr(highs, lows, closes, 14)
            if idx < len(atr) and closes[-1] > 0:
                atr_ratio = atr[idx] / closes[-1]
                scores['atr'] = max(1.0 - min(atr_ratio / 0.03, 1.0), 0.0)
    
    if 'atr' not in scores:
        scores['atr'] = 0.0
    
    # Weighted total
    total = sum(scores[k] * weights[k] for k in weights)
    
    return {
        'total': round(total, 4),
        'components': scores,
        'weights': weights,
    }


def cascade_by_score(base_sigs: List[Dict], data: List[Dict],
                     oi_data: Optional[List[Dict]] = None,
                     score_threshold: float = 0.6,
                     params: Optional[Dict] = None) -> List[Dict]:
    """Filter base signals by quality score instead of binary filters.
    
    Each signal gets a 'quality' dict with total score + component breakdown.
    Only signals with total >= score_threshold are returned.
    
    This is the TRIZ (Principle 6) improvement over detect_cascade_signals:
    instead of ALL binary filters must pass, use a weighted continuous score.
    """
    result = []
    pass_count = 0
    total_count = 0
    
    for sig in base_sigs:
        idx = sig.get('idx')
        if idx is None or idx >= len(data):
            continue
        total_count += 1
        
        quality = compute_quality_score(data, idx, params)
        sig = dict(sig)
        sig['quality'] = quality
        
        if quality['total'] >= score_threshold:
            pass_count += 1
            result.append(sig)
    
    if total_count > 0:
        pass_rate = pass_count / total_count * 100
        avg_score = sum(s['quality']['total'] for s in result) / len(result) if result else 0
        print(f"  Score cascade: thresh={score_threshold:.2f} → {pass_count}/{total_count} "
              f"({pass_rate:.1f}%) pass, avg_score={avg_score:.3f}")
    
    return result
    """
    Apply cascade filters to base signals.
    Only signals passing ALL enabled filters are returned.
    Each signal gets a 'filters' dict showing which filters passed.
    """
    result = []
    for sig in base_sigs:
        idx = sig.get('idx')
        if idx is None:
            continue
        if idx >= len(data):
            continue

        filter_results = apply_filters(data, idx, filters_config, filter_params)
        sig = dict(sig)
        sig['filters'] = filter_results

        # ALL enabled filters must pass
        all_pass = all(v for k, v in filter_results.items() if filters_config.get(k, True))
        if all_pass:
            result.append(sig)

    return result
