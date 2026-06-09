"""
Volume Profile — Value Area / HVN Strategy for MOEX Trading Bot.

Logic:
  - Build volume profile over N periods (N=20 bars)
  - For each price level (bucketized), sum volume
  - HVN (High Volume Node) = level where volume > mean + 2*std
  - LVN (Low Volume Node) = level where volume < mean - 1*std
  - When price reaches HVN from above → LONG (support bounce)
  - When price reaches HVN from below → SHORT (resistance reject)
  - Confirmation: close in direction away from HVN

Signal format:
  {time, ticker, direction, entry, exit, return_pct, strategy='profile', hvn_level}
"""

from typing import List, Dict


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


def _find_hvn_levels(data, lookback=20, vol_mult=2.0, n_buckets=10):
    """
    Find HVN/LVN levels based on volume profile over lookback periods.
    Returns (hvn_level, lvn_level) where:
      hvn_level = weighted avg price of high-volume buckets
      lvn_level = weighted avg price of low-volume buckets
    """
    segment = data[-lookback:] if len(data) > lookback else data
    if len(segment) < 10:
        return None, None

    min_price = min(min(r['low'], r['close']) for r in segment)
    max_price = max(max(r['high'], r['close']) for r in segment)
    price_range = max_price - min_price
    if price_range <= 0:
        return None, None

    bucket_size = price_range / n_buckets
    buckets = {k: {'vol': 0.0, 'count': 0, 'price_sum': 0.0} for k in range(n_buckets)}

    for r in segment:
        avg_price = (r['high'] + r['low']) / 2
        idx = min(int((avg_price - min_price) / bucket_size), n_buckets - 1)
        buckets[idx]['vol'] += r['volume']
        buckets[idx]['count'] += 1
        buckets[idx]['price_sum'] += avg_price

    vol_vals = [b['vol'] for b in buckets.values()]
    mean_v = sum(vol_vals) / len(vol_vals) if vol_vals else 0
    var_v = sum((v - mean_v)**2 for v in vol_vals) / len(vol_vals) if vol_vals else 0
    std_v = var_v**0.5

    hvn_th = mean_v + vol_mult * std_v
    lvn_th = mean_v - 1.0 * std_v

    hvn_buckets = [b for k, b in buckets.items() if b['vol'] > hvn_th and b['count'] > 0]
    lvn_buckets = [b for k, b in buckets.items() if b['vol'] < lvn_th and b['count'] > 0]

    hvn_level = (sum(b['price_sum'] for b in hvn_buckets) / sum(b['count'] for b in hvn_buckets)
                 if hvn_buckets and sum(b['count'] for b in hvn_buckets) > 0 else None)
    lvn_level = (sum(b['price_sum'] for b in lvn_buckets) / sum(b['count'] for b in lvn_buckets)
                 if lvn_buckets and sum(b['count'] for b in lvn_buckets) > 0 else None)

    return hvn_level, lvn_level


def detect_profile_signals(symbol, data, config=None):
    """
    Detect Volume Profile HVN signals.

    Parameters
    ----------
    symbol : str
        Ticker code.
    data : list[dict]
        OHLCV data (must contain open, high, low, close, volume).
    config : dict
        lookback (default 20), vol_mult (default 2.0),
        n_buckets (default 10), horizon (default 12),
        hvn_touch_pct (default 0.01) — how close price must be to HVN.

    Returns
    -------
    list[dict]
    """
    default = {'lookback': 20, 'vol_mult': 2.0, 'n_buckets': 10,
               'horizon': 12, 'hvn_touch_pct': 0.01}
    config = {**default, **(config or {})}

    n = len(data)
    if n < config['lookback'] + 10:
        return []

    lookback = config['lookback']
    horizon = config['horizon']
    touch_pct = config['hvn_touch_pct']

    signals = []
    min_idx = lookback + 10

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        hvn_level, lvn_level = _find_hvn_levels(
            data[max(0, i-lookback):i+1],
            lookback=lookback,
            vol_mult=config['vol_mult'],
            n_buckets=config['n_buckets'],
        )

        if hvn_level is None:
            continue

        close = data[i]['close']
        hvn_diff = abs(close - hvn_level) / max(hvn_level, 1)

        if hvn_diff > touch_pct:
            continue

        # Price is at HVN — determine direction
        # If close > HVN, expect rejection (SHORT)
        # If close < HVN, expect bounce (LONG)
        direction = 'SHORT' if close >= hvn_level else 'LONG'

        entry = data[i+1]['open']
        if entry <= 0:
            continue
        exit_price = data[i+horizon]['close'] if i+horizon < n else data[-1]['close']

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': data[i]['time'],
            'return_pct': round(ret, 4),
            'strategy': 'profile',
            'idx': i,
            'hvn_level': round(hvn_level, 4),
            'lvn_level': round(lvn_level, 4) if lvn_level else None,
        })

    return signals


def detect_profile_signals_limit(symbol, data, config=None):
    """
    Limit-order variant of Volume Profile.
    Entry at HVN level instead of market open.
    """
    default = {'lookback': 20, 'vol_mult': 2.0, 'n_buckets': 10,
               'horizon': 12, 'hvn_touch_pct': 0.01, 'limit_lookback': 5}
    config = {**default, **(config or {})}

    n = len(data)
    if n < config['lookback'] + 10:
        return []

    lookback = config['lookback']
    horizon = config['horizon']
    touch_pct = config['hvn_touch_pct']
    limit_lookback = config['limit_lookback']

    signals = []
    min_idx = lookback + 10

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        hvn_level, _ = _find_hvn_levels(
            data[max(0, i-lookback):i+1],
            lookback=lookback,
            vol_mult=config['vol_mult'],
            n_buckets=config['n_buckets'],
        )

        if hvn_level is None:
            continue

        close = data[i]['close']
        hvn_diff = abs(close - hvn_level) / max(hvn_level, 1)

        if hvn_diff > touch_pct:
            continue

        direction = 'SHORT' if close >= hvn_level else 'LONG'

        # Use HVN level as limit price
        limit_price = hvn_level

        fill_bar = None
        max_j = min(i + 1 + limit_lookback, n)
        for j in range(i + 1, max_j):
            if direction == 'LONG' and data[j]['low'] <= limit_price:
                fill_bar = j
                break
            elif direction == 'SHORT' and data[j]['high'] >= limit_price:
                fill_bar = j
                break

        if fill_bar is None:
            continue

        ex = fill_bar + horizon
        if ex >= n:
            continue

        entry = limit_price
        if entry <= 0:
            continue
        exit_price = data[ex]['close']

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': data[i]['time'],
            'return_pct': round(ret, 4),
            'strategy': 'profile',
            'idx': i,
            'fill_bar': fill_bar,
            'limit_price': round(limit_price, 4),
            'hvn_level': round(hvn_level, 4),
        })

    return signals
