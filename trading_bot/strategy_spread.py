"""
Pair Trading (Spread Trading) — Strategy for MOEX Trading Bot.

Logic:
  - Pairs of correlated instruments: Si/BR (USD/RUB & Brent),
    RI/GL (RTS & Gold), NM/AF (NorNickel & Africa)
  - Calculate 60-minute spread ratio between pair
  - z-score of spread over lookback period
  - z-score > entry_th = SHORT spread (sell ratio, buy denominator)
  - z-score < -entry_th = LONG spread (buy ratio, sell denominator)
  - Exit when z-score < exit_th (reversion to mean)

Signal format:
  direction='LONG' means: go long on ticker1, short on ticker2
  direction='SHORT' means: go short on ticker1, long on ticker2
  entry/exit = z-score values (normalized for signal comparison)
  return_pct = combined return of both legs
"""

from typing import List, Dict


# Correlated pairs (ticker1, ticker2, reason)
PAIRS = [
    ('Si', 'BR', 'USD/RUB — Brent (commodity FX)'),
    ('RI', 'GL', 'RTS Index — Gold (risk proxy)'),
    ('NM', 'AF', 'NorNickel — Africa (base metals)'),
    ('CNYRUBF', 'EURRUBF', 'CNY/RUB — EUR/RUB (EM FX)'),
    ('BR', 'NG', 'Brent — Gas (energy complex)'),
]


def _zs(vals, w=60):
    """Rolling z-score, NO look-ahead."""
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x-mu)**2 for x in chunk) / w
        sd = var**0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def align_pairs(data1, data2):
    """
    Align two time series on time.
    Returns list of (t1, t2, merged_time) tuples where both have data.
    """
    by_time = {}
    for r in data1:
        t = r['time'][:16]
        by_time[t] = {'c1': r['close'], 't1': r['time']}
    aligned = []
    for r in data2:
        t = r['time'][:16]
        if t in by_time:
            aligned.append({
                'time': by_time[t]['t1'],
                'close1': by_time[t]['c1'],
                'close2': r['close'],
            })
    return aligned


def detect_spread_signals(symbol, data, config=None):
    """
    Detect pair trading signals.

    NOTE: This strategy works differently — it expects `symbol` to be the pair
    name (e.g. 'Si/BR') and `data` to be a list of merged dicts with field
    'close_ratio' = close1 / close2.

    Alternatively, call detect_spread_signals_for_pair(pair_name, data1, data2, config).
    """
    default = {'entry_z': 2.0, 'exit_z': 0.5, 'horizon': 12, 'lookback': 60}
    config = {**default, **(config or {})}

    n = len(data)
    if n < config['lookback'] + 10:
        return []

    prices = [d['close_ratio'] for d in data]
    spread_z = _zs(prices, config['lookback'])

    signals = []
    min_idx = config['lookback'] + 5
    entry_th = config['entry_z']
    exit_th = config['exit_z']

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + config['horizon'] >= n:
            continue

        z = spread_z[i]

        if z > entry_th:
            direction = 'SHORT'
        elif z < -entry_th:
            direction = 'LONG'
        else:
            continue

        entry = data[i+1].get('open_ratio', prices[i])
        if entry <= 0:
            continue
        exit_price = prices[i + config['horizon']]

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 6),
            'exit': round(exit_price, 6),
            'time': data[i]['time'],
            'return_pct': round(ret, 4),
            'strategy': 'spread',
            'idx': i,
            'spread_z': round(z, 4),
        })

    return signals


def detect_spread_signals_for_pair(pair_name, data1, data2, config=None):
    """
    Detect spread signals for a specific pair of instruments.

    Parameters
    ----------
    pair_name : str
        e.g. 'Si/BR'
    data1 : list[dict]
        OHLCV data for first instrument.
    data2 : list[dict]
        OHLCV data for second instrument.
    config : dict
        entry_z, exit_z, horizon, lookback.

    Returns
    -------
    list[dict]
    """
    aligned = align_pairs(data1, data2)
    if len(aligned) < 100:
        return []

    # Build merged data with close_ratio
    merged = []
    for r in aligned:
        ratio = r['close1'] / r['close2'] if r['close2'] > 0 else 1.0
        merged.append({
            'time': r['time'],
            'close_ratio': ratio,
            'open_ratio': ratio,
        })

    return detect_spread_signals(pair_name, merged, config)


def detect_spread_signals_limit(symbol, data, config=None):
    """Limit-order variant not applicable for spread trading (market entry)."""
    return detect_spread_signals(symbol, data, config)
