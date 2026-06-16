"""
Order Block Engine — Variant D (Limit at OB Level).

Логика (ICT Smart Money с лимитными ордерами):
  1. Загружаем 5m данные, ресемплим до H1
  2. Находим displacement: body > 1.5× медиана тела за 20 баров И range > 1.2× медиана range
  3. Order Block = свеча ПЕРЕД displacement
  4. Лимитный ордер на уровне OB:
     - LONG: limit BUY на low[ob_idx]
     - SHORT: limit SELL на high[ob_idx]
  5. Ждём до limit_lookback=5 баров H1 для исполнения
  6. Если заполнился: entry = уровень OB, exit = close[fill_bar + horizon]
  7. Возвращаем только свежие сигналы (последние limit_lookback + 1 баров)

Параметры:
  body_mul=1.5, range_mul=1.2, lookback=20
  horizon=2, limit_lookback=5, max_signal_age=6
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from datetime import datetime, timedelta, timezone


def _rolling_median(arr, w=20):
    """Fast rolling median over PREVIOUS w values. No look-ahead."""
    s = pd.Series(arr)
    out = s.rolling(window=w, min_periods=1).median().shift(1)
    out[:1] = arr[0]
    return out.ffill().fillna(arr[0]).values


def load_price_data(symbol: str, days: int = 30) -> List[Tuple]:
    """Load 5m OHLCV from DB."""
    import psycopg2

    DB = dict(host='127.0.0.1', port=5432, dbname='moex', user='postgres', password='postgres')
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    conn = psycopg2.connect(**DB)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT time, open, high, low, close, volume
            FROM moex_prices_5m
            WHERE symbol = %s AND time >= %s
            ORDER BY time
        """, (symbol, since))
        for rec in cur:
            time_str = rec[0].isoformat() if hasattr(rec[0], 'isoformat') else str(rec[0])
            rows.append((time_str, float(rec[1]), float(rec[2]), float(rec[3]), float(rec[4]), float(rec[5])))
        cur.close()
    finally:
        conn.close()
    return rows


def resample_h1(rows: List[Tuple], rule: str = '1h') -> pd.DataFrame:
    """Resample 5m data to target TF OHLCV."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    resampled = df.resample(rule).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
    })
    resampled.dropna(inplace=True)
    return resampled


def detect_order_block_signals(
    symbol: str,
    rows: List[Tuple],
    config: dict,
) -> List[Dict]:
    """
    Detect Order Block signals (Variant D — Limit at OB Level).

    Parameters
    ----------
    symbol : str
        Ticker code.
    rows : List[Tuple]
        5m OHLCV: (time, open, high, low, close, volume), asc.
    config : dict
        body_mul, range_mul, lookback, horizon, limit_lookback, max_signal_age

    Returns
    -------
    List[Dict]
        Signals with ticker, direction, entry, exit, time, strategy='order_block'.
    """
    body_mul = config.get('body_mul', 1.5)
    range_mul = config.get('range_mul', 1.2)
    lookback = config.get('lookback', 20)
    horizon = config.get('horizon', 2)
    limit_lookback = config.get('limit_lookback', 5)
    max_signal_age = config.get('max_signal_age', 6)
    min_history = config.get('min_history', 100)
    resample_rule = config.get('tf', 'H1')

    # Map TF names to pandas rules
    rule_map = {'5m': '5min', '15m': '15min', '30m': '30min', 'H1': '1h', 'H2': '2h', 'H4': '4h'}
    rule = rule_map.get(resample_rule, '1h')

    # Resample to target TF
    df = resample_h1(rows, rule)
    n = len(df)
    if n < min_history:
        return []

    o = df['open'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)
    c = df['close'].values.astype(float)
    times = df.index

    # Compute indicators
    bodies = np.abs(c - o)
    ranges = h - l
    med_body = _rolling_median(bodies, lookback)
    med_range = _rolling_median(ranges, lookback)

    # Find all displacements
    displ = []
    for i in range(lookback + 1, n):
        if bodies[i] <= 0 or ranges[i] <= 0 or med_body[i] <= 0 or med_range[i] <= 0:
            continue
        if bodies[i] > med_body[i] * body_mul and ranges[i] > med_range[i] * range_mul:
            direction = 'LONG' if c[i] > o[i] else 'SHORT'
            displ.append({'idx': i, 'direction': direction, 'ob_idx': i - 1})

    signals = []
    for d in displ:
        i = d['idx']
        direction = d['direction']
        ob_idx = d['ob_idx']

        # OB level
        if direction == 'LONG':
            level = l[ob_idx]
        else:
            level = h[ob_idx]

        # Look for limit fill within limit_lookback H1 bars
        fill_bar = None
        for j in range(i, min(i + limit_lookback, n)):
            if direction == 'LONG' and l[j] <= level:
                fill_bar = j
                break
            elif direction == 'SHORT' and h[j] >= level:
                fill_bar = j
                break

        if fill_bar is None:
            continue

        # Exit after horizon from fill
        ex = fill_bar + horizon
        if ex >= n:
            continue

        entry = level
        exit_price = c[ex]

        if direction == 'LONG':
            return_pct = (exit_price - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_price) / entry * 100.0

        # Only signal if recent enough (within max_signal_age bars of now)
        signal_age = n - 1 - fill_bar
        if signal_age > max_signal_age:
            continue

        signal = {
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': times[ob_idx].isoformat(),
            'return_pct': round(return_pct, 4),
            'strategy': 'order_block',
            'idx': ob_idx,
            'ob_level': round(level, 4),
            'fill_bar': fill_bar,
            'horizon': horizon,
        }
        signals.append(signal)

    return signals
