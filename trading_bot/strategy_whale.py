"""
Whale Detection (OI Volume Burst) — Strategy for MOEX Trading Bot.

Logic:
  - z-score of yur_buy (institutional buying volume) over 20 periods
  - z-score of yur_sell (institutional selling volume) over 20 periods
  - Signal when yur_buy z-score > threshold (whale buying) OR
    yur_sell z-score > threshold (whale selling)
  - Confirmation: fiz (retail) volume is NOT extreme (filter noise)
  - Entry at next bar open
  - Exit after horizon bars
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


def detect_whale_signals(symbol, data, config=None):
    """
    Detect Whale OI Volume Burst signals.

    Parameters
    ----------
    symbol : str
        Ticker code.
    data : list[dict]
        Merged OHLCV+OI data (must contain yur_buy, yur_sell, fiz_buy, fiz_sell).
    config : dict
        yur_z_thresh (default 2.5), horizon (default 12),
        fiz_z_max (default 1.5) — max fiz z-score to filter retail noise.

    Returns
    -------
    list[dict] — signals with ticker, direction, entry, exit, time, return_pct, strategy='whale'
    """
    default = {'yur_z_thresh': 2.5, 'horizon': 12, 'fiz_z_max': 1.5}
    config = {**default, **(config or {})}

    n = len(data)
    if n < 50:
        return []

    th = config['yur_z_thresh']
    horizon = config['horizon']
    fiz_max = config['fiz_z_max']

    yur_buy = [r['yur_buy'] for r in data]
    yur_sell = [r['yur_sell'] for r in data]
    fiz_buy = [r['fiz_buy'] for r in data]
    fiz_sell = [r['fiz_sell'] for r in data]

    buy_z = _zs(yur_buy, 20)
    sell_z = _zs(yur_sell, 20)
    fiz_total = [fiz_buy[i] + fiz_sell[i] for i in range(n)]
    fiz_z = _zs(fiz_total, 20)

    signals = []
    min_idx = 25

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        # Whale condition: yur z-score burst
        whale_buy = buy_z[i] > th
        whale_sell = sell_z[i] > th

        if not whale_buy and not whale_sell:
            continue

        # Filter: retail should not be extreme (avoid noise)
        if abs(fiz_z[i]) > fiz_max:
            continue

        direction = 'LONG' if whale_buy else 'SHORT'
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
            'strategy': 'whale',
            'idx': i,
            'yur_buy_z': round(buy_z[i], 4),
            'yur_sell_z': round(sell_z[i], 4),
        })

    return signals


def detect_whale_signals_limit(symbol, data, config=None):
    """
    Limit-order variant of Whale Detection.
    LONG: limit_price = low[i], fill when low[j] <= limit_price
    SHORT: limit_price = high[i], fill when high[j] >= limit_price
    Search fill within limit_lookback bars after trigger.
    """
    default = {'yur_z_thresh': 2.5, 'horizon': 12, 'fiz_z_max': 1.5, 'limit_lookback': 5}
    config = {**default, **(config or {})}

    n = len(data)
    if n < 50:
        return []

    th = config['yur_z_thresh']
    horizon = config['horizon']
    fiz_max = config['fiz_z_max']
    limit_lookback = config['limit_lookback']

    yur_buy = [r['yur_buy'] for r in data]
    yur_sell = [r['yur_sell'] for r in data]
    fiz_buy = [r['fiz_buy'] for r in data]
    fiz_sell = [r['fiz_sell'] for r in data]
    highs = [r['high'] for r in data]
    lows = [r['low'] for r in data]

    buy_z = _zs(yur_buy, 20)
    sell_z = _zs(yur_sell, 20)
    fiz_total = [fiz_buy[i] + fiz_sell[i] for i in range(n)]
    fiz_z = _zs(fiz_total, 20)

    signals = []
    min_idx = 25

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        whale_buy = buy_z[i] > th
        whale_sell = sell_z[i] > th

        if not whale_buy and not whale_sell:
            continue

        if abs(fiz_z[i]) > fiz_max:
            continue

        direction = 'LONG' if whale_buy else 'SHORT'

        if direction == 'LONG':
            limit_price = lows[i]
        else:
            limit_price = highs[i]

        fill_bar = None
        max_j = min(i + 1 + limit_lookback, n)
        for j in range(i + 1, max_j):
            if direction == 'LONG' and lows[j] <= limit_price:
                fill_bar = j
                break
            elif direction == 'SHORT' and highs[j] >= limit_price:
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
            'strategy': 'whale',
            'idx': i,
            'fill_bar': fill_bar,
            'limit_price': round(limit_price, 4),
            'yur_buy_z': round(buy_z[i], 4),
            'yur_sell_z': round(sell_z[i], 4),
        })

    return signals
