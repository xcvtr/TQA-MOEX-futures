"""
Momentum Breakout with OI Confirmation — Strategy for MOEX Trading Bot.

Logic:
  - Close[i] > max(High[i-20:i]) = new 20-period high → LONG signal
  - Close[i] < min(Low[i-20:i]) = new 20-period low → SHORT signal
  - OI confirmation: total_oi[i] > total_oi[i-1] (OI growing)
  - yur participation: yur_buy > fiz_buy for LONG, yur_sell > fiz_sell for SHORT
  - Entry at next bar open
  - Exit after horizon bars (default 24) or stop-loss

Signal format:
  {time, ticker, direction, entry, exit, return_pct, strategy='momentum'}
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


def detect_momentum_signals(symbol, data, config=None):
    """
    Detect Momentum Breakout + OI Confirmation signals.

    Parameters
    ----------
    symbol : str
        Ticker code.
    data : list[dict]
        Merged OHLCV+OI data (must contain open, high, low, close, volume,
        total_oi, yur_buy, yur_sell, fiz_buy, fiz_sell).
    config : dict
        lookback (default 20), horizon (default 24),
        oi_growth_min (default 0.0) — minimum OI growth threshold,
        require_yur_dom (default True) — require yur > fiz confirmation.

    Returns
    -------
    list[dict]
    """
    default = {'lookback': 20, 'horizon': 24, 'oi_growth_min': 0.0,
               'require_yur_dom': True}
    config = {**default, **(config or {})}

    n = len(data)
    if n < config['lookback'] + 10:
        return []

    lookback = config['lookback']
    horizon = config['horizon']
    oi_min = config['oi_growth_min']
    require_yur = config['require_yur_dom']

    closes = [r['close'] for r in data]
    highs = [r['high'] for r in data]
    lows = [r['low'] for r in data]
    total_oi = [r.get('total_oi', 0) for r in data]
    yur_buy = [r.get('yur_buy', 0) for r in data]
    yur_sell = [r.get('yur_sell', 0) for r in data]
    fiz_buy = [r.get('fiz_buy', 0) for r in data]
    fiz_sell = [r.get('fiz_sell', 0) for r in data]

    signals = []
    min_idx = lookback + 5

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        # Breakout detection
        high_max = max(highs[i-lookback:i])
        low_min = min(lows[i-lookback:i])

        is_breakout_up = closes[i] > high_max
        is_breakout_dn = closes[i] < low_min

        if not is_breakout_up and not is_breakout_dn:
            continue

        # OI confirmation
        oi_change = total_oi[i] - total_oi[i-1] if i > 0 else 0
        if oi_change < oi_min:
            continue

        # yur dominance confirmation
        if require_yur:
            if is_breakout_up and yur_buy[i] <= fiz_buy[i]:
                continue
            if is_breakout_dn and yur_sell[i] <= fiz_sell[i]:
                continue

        direction = 'LONG' if is_breakout_up else 'SHORT'
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
            'strategy': 'momentum',
            'idx': i,
            'oi_change': round(oi_change, 2),
        })

    return signals


def detect_momentum_signals_limit(symbol, data, config=None):
    """
    Limit-order variant of Momentum Breakout.
    LONG: limit_price = low[i], fill when low[j] <= limit_price
    SHORT: limit_price = high[i], fill when high[j] >= limit_price
    """
    default = {'lookback': 20, 'horizon': 24, 'oi_growth_min': 0.0,
               'require_yur_dom': True, 'limit_lookback': 5}
    config = {**default, **(config or {})}

    n = len(data)
    if n < config['lookback'] + 10:
        return []

    lookback = config['lookback']
    horizon = config['horizon']
    oi_min = config['oi_growth_min']
    require_yur = config['require_yur_dom']
    limit_lookback = config['limit_lookback']

    closes = [r['close'] for r in data]
    highs = [r['high'] for r in data]
    lows = [r['low'] for r in data]
    total_oi = [r.get('total_oi', 0) for r in data]
    yur_buy = [r.get('yur_buy', 0) for r in data]
    yur_sell = [r.get('yur_sell', 0) for r in data]
    fiz_buy = [r.get('fiz_buy', 0) for r in data]
    fiz_sell = [r.get('fiz_sell', 0) for r in data]

    signals = []
    min_idx = lookback + 5

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        high_max = max(highs[i-lookback:i])
        low_min = min(lows[i-lookback:i])

        is_breakout_up = closes[i] > high_max
        is_breakout_dn = closes[i] < low_min

        if not is_breakout_up and not is_breakout_dn:
            continue

        oi_change = total_oi[i] - total_oi[i-1] if i > 0 else 0
        if oi_change < oi_min:
            continue

        if require_yur:
            if is_breakout_up and yur_buy[i] <= fiz_buy[i]:
                continue
            if is_breakout_dn and yur_sell[i] <= fiz_sell[i]:
                continue

        direction = 'LONG' if is_breakout_up else 'SHORT'

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
            'strategy': 'momentum',
            'idx': i,
            'fill_bar': fill_bar,
            'limit_price': round(limit_price, 4),
            'oi_change': round(oi_change, 2),
        })

    return signals
