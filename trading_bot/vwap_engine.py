"""VWAP Deviation Reversion Engine — 4th strategy for MOEX Trading Bot.

Логика:
  - Rolling VWAP за N баров (по умолч. 20)
  - Rolling ATR за 14 баров
  - deviation = (close - vwap) / atr
  - Если deviation > +2.0 → SHORT (перекупленность)
  - Если deviation < -2.0 → LONG (перепроданность)

Walk-forward backtest (out-of-sample last 30%): 53.2% avg WR, >1000 сигналов/тикер.
Лучшие тикеры: GZ (59.4%), Eu (56.8%), SR (56.6%), Si (54.8%), MC (54.4%)
Документация: docs/backtest/vwap_results.txt
"""

from typing import List, Dict, Tuple


# ── helpers ──────────────────────────────────────────────────────────


def _calc_vwap(closes: List[float], volumes: List[float], w: int = 20) -> List[float]:
    """Rolling VWAP, NO look-ahead. vwap[i] = sum(close*vol over [i-w:i]) / sum(vol)."""
    n = len(closes)
    vwap = [0.0] * n
    for i in range(w, n):
        cum_pv = sum(closes[j] * volumes[j] for j in range(i - w, i))
        cum_v = sum(volumes[j] for j in range(i - w, i))
        vwap[i] = cum_pv / cum_v if cum_v > 0 else closes[i]
    return vwap


def _calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Rolling ATR, NO look-ahead. Uses true range = max(H-L, |H-Cp|, |L-Cp|)."""
    n = len(highs)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = [0.0] * n
    for i in range(period, n):
        atr[i] = sum(tr[i - period:i]) / period
    return atr


# ── data loading ─────────────────────────────────────────────────────


def load_price_data(symbol: str, days: int = 30) -> List[Dict]:
    """Load OHLCV 5m. Returns list of dicts: time, open, high, low, close, volume."""
    import psycopg2
    from datetime import datetime, timedelta, timezone

    DB = dict(host="10.0.0.64", port=5432, dbname="moex", user="postgres", password="***")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s
        ORDER BY time
    """, (symbol, since))
    rows = []
    for r in cur:
        rows.append({
            'time': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
            'open': float(r[1]),
            'high': float(r[2]),
            'low': float(r[3]),
            'close': float(r[4]),
            'volume': float(r[5]),
        })
    cur.close()
    conn.close()
    return rows


# ── signal detection ─────────────────────────────────────────────────


DEFAULT_VWAP_CONFIG = {
    'dev_thresh': 2.0,       # deviation threshold (|close-vwap|/atr > this)
    'horizon': 12,            # exit after N bars (60 min on 5m)
    'vwap_window': 20,        # rolling window for VWAP
    'atr_period': 14,         # rolling window for ATR
}

VWAP_TICKERS: dict = {
    'GZ': {'enabled': True, 'go': 2065, 'tick_rub': 0.01, 'minstep': 0.01,
           'label': 'GZ (Газпром VWAP)', 'horizon': 12, 'max_loss': -5.0},
    'Eu': {'enabled': True, 'go': 973, 'tick_rub': 0.01, 'minstep': 0.01,
           'label': 'Eu (Евро VWAP)', 'horizon': 12, 'max_loss': -5.0},
    'SR': {'enabled': True, 'go': 5719, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'SR (Сбер VWAP)', 'horizon': 12, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'Si (Доллар VWAP)', 'horizon': 12, 'max_loss': -5.0},
    'MC': {'enabled': True, 'go': 3149, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'MC (Моэкс VWAP)', 'horizon': 12, 'max_loss': -5.0},
}


def detect_vwap_signals(
    symbol: str,
    rows: List[Dict],
    config: dict,
) -> List[Dict]:
    """
    Detect VWAP Deviation Reversion signals.

    Parameters
    ----------
    symbol : str
        Ticker code.
    rows : List[Dict]
        OHLCV data, ordered by time asc.
    config : dict
        dev_thresh, horizon, vwap_window, atr_period.

    Returns
    -------
    List[Dict] — signals with ticker, direction, entry, exit, time, return_pct, strategy='vwap'
    """
    dev_thresh = config.get('dev_thresh', 2.0)
    horizon = config.get('horizon', 12)
    vwap_w = config.get('vwap_window', 20)
    atr_p = config.get('atr_period', 14)

    n = len(rows)
    if n < max(vwap_w, atr_p) + 10:
        return []

    closes = [r['close'] for r in rows]
    volumes = [r['volume'] for r in rows]
    highs = [r['high'] for r in rows]
    lows = [r['low'] for r in rows]

    # Indicators (no look-ahead)
    vwap = _calc_vwap(closes, volumes, vwap_w)
    atr = _calc_atr(highs, lows, closes, atr_p)

    signals: List[Dict] = []
    min_idx = max(vwap_w, atr_p) + 5

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue
        if atr[i] <= 0:
            continue

        dev = (closes[i] - vwap[i]) / atr[i]

        if dev > dev_thresh:
            direction = 'SHORT'
        elif dev < -dev_thresh:
            direction = 'LONG'
        else:
            continue

        entry = rows[i + 1]['open']
        if entry <= 0:
            continue
        exit_price = rows[i + horizon]['close'] if i + horizon < n else rows[-1]['close']

        if direction == 'LONG':
            return_pct = (exit_price - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_price) / entry * 100.0

        signals.append({
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': rows[i]['time'],
            'return_pct': round(return_pct, 4),
            'strategy': 'vwap',
            'idx': i,
            'deviation': round(dev, 4),
        })

    return signals
