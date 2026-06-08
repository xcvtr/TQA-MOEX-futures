"""
Reversion Engine — Mean Reversion After Volatility Exhaustion (MRAVE).

Functions:
    load_price_data(symbol, days=30)       — load OHLCV 5m data from DB
    detect_mean_reversion_signals(...)     — detect mean reversion signals
"""

from typing import List, Dict, Tuple, Optional


# ── helpers ──────────────────────────────────────────────────────────────────


def _zs(vals: List[float], w: int = 20) -> List[float]:
    """Rolling z-score, NO look-ahead."""
    out: List[float] = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i - w:i]
        mu = sum(chunk) / w
        var = sum((x - mu) ** 2 for x in chunk) / w
        sd = var ** 0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def _rolling_median(arr: List[float], w: int = 50) -> List[float]:
    """Rolling median over PREVIOUS w values, NO look-ahead. Excludes current value."""
    out: List[float] = [0.0] * len(arr)
    for i in range(len(arr)):
        if i == 0:
            win = [arr[0]]
        elif i < w:
            win = arr[:i]
        else:
            win = arr[i - w:i]
        if not win:
            win = [arr[0]]
        sorted_win = sorted(win)
        mid = len(sorted_win) // 2
        if len(sorted_win) % 2 == 0:
            out[i] = (sorted_win[mid - 1] + sorted_win[mid]) / 2.0
        else:
            out[i] = float(sorted_win[mid])
    return out


# ── data loading ────────────────────────────────────────────────────────────


def load_price_data(symbol: str, days: int = 30) -> List[Tuple[str, float, float, float, float, float]]:
    """
    Load raw OHLCV 5m price data from DB (no OI join).

    Returns list of tuples (time, open, high, low, close, volume),
    ordered by time asc.
    """
    import psycopg2
    from . import DB_CREDENTIALS
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows: List[Tuple[str, float, float, float, float, float]] = []
    conn = psycopg2.connect(**DB_CREDENTIALS)
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
            rows.append((
                time_str,
                float(rec[1]),  # open
                float(rec[2]),  # high
                float(rec[3]),  # low
                float(rec[4]),  # close
                float(rec[5]),  # volume
            ))
        cur.close()
    finally:
        conn.close()
    return rows


# ── signal detection ────────────────────────────────────────────────────────


def detect_mean_reversion_signals(
    symbol: str,
    rows: List[Tuple[str, float, float, float, float, float]],
    config: dict,
) -> List[Dict[str, object]]:
    """
    Detect Mean Reversion After Volatility Exhaustion signals.

    Parameters
    ----------
    symbol : str
        Ticker code.
    rows : List[Tuple[str, float, float, float, float, float]]
        List of (time, open, high, low, close, volume) tuples, ordered by time asc.
    config : dict
        Configuration with keys:
            mid_low       — lower bound for position in range (default 0.3)
            mid_high      — upper bound for position in range (default 0.7)
            horizon       — exit horizon in bars (default 12)
            vol_thresh    — volume z-score threshold (default 1.5)
            range_mul     — range multiplier vs median range (default 1.5)
            lookback_bars — number of prior bars for pattern check (default 3)

    Returns
    -------
    List[Dict[str, object]]
        List of Signal dicts with keys:
            ticker, direction, entry, exit, time, vol_z, return_pct,
            strategy='reversion', idx
    """
    mid_low = config.get('mid_low', 0.3)
    mid_high = config.get('mid_high', 0.7)
    horizon = config.get('horizon', 12)
    vol_thresh = config.get('vol_thresh', 1.5)
    range_mul = config.get('range_mul', 1.5)
    lookback = config.get('lookback_bars', 3)

    n = len(rows)
    if n < 50:
        return []

    # Extract arrays
    times = [r[0] for r in rows]
    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]

    # Compute indicators (no look-ahead)
    rng = [highs[i] - lows[i] for i in range(n)]
    wz = _zs(volumes, 20)
    pos = [(closes[i] - lows[i]) / max(rng[i], 0.001) for i in range(n)]
    mr = _rolling_median(rng, 50)

    signals: List[Dict[str, object]] = []
    min_idx = 25  # need enough history for z-score + median

    for i in range(min_idx, n):
        # Need next bar for entry and horizon bars for exit
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        # Condition 1: volume z-score threshold
        if wz[i] < vol_thresh:
            continue

        # Condition 2: range >= median range * multiplier
        if rng[i] < mr[i] * range_mul:
            continue

        # Condition 3: position in mid-range
        if pos[i] < mid_low or pos[i] > mid_high:
            continue

        # Condition 4: bar pattern — 3 prior bars
        if i < lookback:
            continue

        prior_closes = closes[i - lookback:i]
        prior_opens = opens[i - lookback:i]
        prior_cc = [prior_closes[j] - prior_opens[j] for j in range(lookback)]

        # All 3 prior bars: close > open → SHORT signal
        # All 3 prior bars: close < open → LONG signal
        all_up = all(pc > 0 for pc in prior_cc)
        all_down = all(pc < 0 for pc in prior_cc)

        if not all_up and not all_down:
            continue

        direction = 'SHORT' if all_up else 'LONG'

        entry = opens[i + 1]
        if entry <= 0:
            continue

        exit_price = closes[i + horizon]

        if direction == 'LONG':
            return_pct = (exit_price - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_price) / entry * 100.0

        signals.append({
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': times[i],
            'vol_z': round(wz[i], 4),
            'return_pct': round(return_pct, 4),
            'strategy': 'reversion',
            'idx': i,
        })

    return signals


def detect_mean_reversion_signals_limit(
    symbol: str,
    rows: List[Tuple[str, float, float, float, float, float]],
    config: dict,
) -> List[Dict[str, object]]:
    """
    Limit-order variant of Mean Reversion.

    LONG: limit_price = low[i], fill when low[j] <= limit_price
    SHORT: limit_price = high[i], fill when high[j] >= limit_price
    Search fill within limit_lookback bars after trigger.
    """
    mid_low = config.get('mid_low', 0.3)
    mid_high = config.get('mid_high', 0.7)
    horizon = config.get('horizon', 12)
    vol_thresh = config.get('vol_thresh', 1.5)
    range_mul = config.get('range_mul', 1.5)
    lookback = config.get('lookback_bars', 3)
    limit_lookback = config.get('limit_lookback', 5)

    n = len(rows)
    if n < 50:
        return []

    times = [r[0] for r in rows]
    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]

    rng = [highs[i] - lows[i] for i in range(n)]
    wz = _zs(volumes, 20)
    pos = [(closes[i] - lows[i]) / max(rng[i], 0.001) for i in range(n)]
    mr = _rolling_median(rng, 50)

    signals: List[Dict[str, object]] = []
    min_idx = 25

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        if wz[i] < vol_thresh:
            continue
        if rng[i] < mr[i] * range_mul:
            continue
        if pos[i] < mid_low or pos[i] > mid_high:
            continue
        if i < lookback:
            continue

        prior_closes = closes[i - lookback:i]
        prior_opens = opens[i - lookback:i]
        prior_cc = [prior_closes[j] - prior_opens[j] for j in range(lookback)]

        all_up = all(pc > 0 for pc in prior_cc)
        all_down = all(pc < 0 for pc in prior_cc)

        if not all_up and not all_down:
            continue

        direction = 'SHORT' if all_up else 'LONG'

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

        exit_price = closes[ex]

        if direction == 'LONG':
            return_pct = (exit_price - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_price) / entry * 100.0

        signals.append({
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': times[i],
            'vol_z': round(wz[i], 4),
            'return_pct': round(return_pct, 4),
            'strategy': 'reversion',
            'idx': i,
            'fill_bar': fill_bar,
            'limit_price': round(limit_price, 4),
        })

    return signals
