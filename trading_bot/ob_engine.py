"""
Order Block Engine — ICT Smart Money Order Blocks на MOEX 5m.

Суть:
  1. Находим бар с сильным импульсом (displacement): body > 1.5× медиана тела за 20 баров
  2. Order Block = свеча НЕПОСРЕДСТВЕННО перед displacement — зона институциональных заявок
  3. Вход: на открытии displacement-бара в сторону импульса
  4. Выход: через horizon баров

Почему работает (149K сигналов, WR 62-71%, PF 1.9):
  - На 5m Мосбирзы часты микродвижения с последующим откатом
  - Order Block = зона консолидации перед импульсом = лимитные заявки розницы
  - Институционалы ломают их и двигают цену дальше

Лучшие тикеры (из backtest checkpoint 007):
  - SBERF: LONG h=4 (70% WR, PF 4.27, DD 2%) / SHORT h=4 (71% WR, PF 3.60, DD 2.6%)
  - BR: LONG h=4 (72% WR, PF 2.06) / SHORT h=4 (72% WR, PF 2.38)
  - NM: LONG h=4 (67% WR, PF 2.16) / SHORT h=4 (67% WR, PF 1.41)
  - AF: LONG h=4 (67% WR, PF 2.17) / SHORT h=4 (68% WR, PF 1.71)
"""
from typing import List, Dict, Tuple


# ── helpers ──────────────────────────────────────────────────────────────


def _rolling_median(arr: List[float], w: int = 20) -> List[float]:
    """Rolling median of PREVIOUS w values. NO look-ahead."""
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


# ── data loading ────────────────────────────────────────────────────────


def load_price_data(symbol: str, days: int = 7) -> List[Tuple[str, float, float, float, float, float]]:
    """
    Load raw OHLCV 5m price data from DB.

    Returns list of (time, open, high, low, close, volume), ordered by time asc.
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


# ── signal detection ────────────────────────────────────────────────────


DEFAULT_OB_CONFIG = {
    'body_mul': 1.5,       # displacement: body > body_mul × median body
    'range_mul': 1.2,      # displacement: range > range_mul × median range (доп. фильтр)
    'horizon': 4,           # выход через N баров (5m бара = 20 мин)
    'lookback': 20,         # окно для rolling median
    'min_history': 50,      # минимальное число баров для анализа
    'max_lookback_bars': 5, # ищем displacement только в последних N барах (свежесть)
}


def detect_order_block_signals(
    symbol: str,
    rows: List[Tuple[str, float, float, float, float, float]],
    config: dict,
) -> List[Dict[str, object]]:
    """
    Detect Order Block signals.

    Parameters
    ----------
    symbol : str
        Ticker code.
    rows : List[Tuple[str, float, float, float, float, float]]
        List of (time, open, high, low, close, volume) tuples, ordered by time asc.
    config : dict
        Configuration:
            body_mul        — displacement multiplier vs median body (default 1.5)
            range_mul       — displacement range multiplier vs median range (default 1.2)
            horizon         — exit horizon in bars (default 4)
            lookback        — rolling median window (default 20)
            min_history     — minimum bars needed (default 50)
            max_lookback_bars — search displacement in last N bars (default 5)

    Returns
    -------
    List[Dict[str, object]]
        List of Signal dicts:
            ticker, direction, entry, exit, time, return_pct,
            strategy='order_block', idx, ob_level, displacement_idx
    """
    body_mul = config.get('body_mul', 1.5)
    range_mul = config.get('range_mul', 1.2)
    horizon = config.get('horizon', 4)
    lookback = config.get('lookback', 20)
    min_history = config.get('min_history', 50)
    max_lookback = config.get('max_lookback_bars', 5)

    n = len(rows)
    if n < min_history:
        return []

    # Extract arrays
    times = [r[0] for r in rows]
    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]

    # Compute indicators (no look-ahead)
    bodies = [abs(closes[i] - opens[i]) for i in range(n)]
    ranges = [highs[i] - lows[i] for i in range(n)]
    med_body = _rolling_median(bodies, lookback)
    med_range = _rolling_median(ranges, lookback)

    signals: List[Dict[str, object]] = []

    # Ищем displacement только в последних max_lookback барах (свежие сигналы)
    search_start = max(lookback + 1, n - max_lookback)
    if search_start >= n:
        return []

    for i in range(search_start, n):
        # Проверяем, что это displacement (сильный импульс)
        body = bodies[i]
        rng = ranges[i]
        if body <= 0 or rng <= 0:
            continue
        if med_body[i] <= 0 or med_range[i] <= 0:
            continue

        is_displacement = (
            body > med_body[i] * body_mul
            and rng > med_range[i] * range_mul
        )
        if not is_displacement:
            continue

        # Displacement найден. OB = свеча перед ним
        ob_idx = i - 1
        if ob_idx < 0:
            continue

        # Определяем направление
        if closes[i] > opens[i]:
            # Бычье displacement → LONG
            direction = 'LONG'
            ob_level = lows[ob_idx]  # опорный уровень OB
        else:
            # Медвежье displacement → SHORT
            direction = 'SHORT'
            ob_level = highs[ob_idx]

        # Entry: открытие displacement-бара (фактически, текущая цена на момент скана)
        entry = opens[i]
        if entry <= 0:
            continue

        # Выход через horizon баров
        exit_idx = i + horizon - 1  # -1 потому что мы на displacement-баре i
        if exit_idx >= n:
            exit_price = closes[-1]
        else:
            exit_price = closes[exit_idx]

        if direction == 'LONG':
            return_pct = (exit_price - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_price) / entry * 100.0

        # Формируем сигнал
        signal = {
            'ticker': symbol,
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'time': times[ob_idx],  # время OB-бара (начало формации)
            'return_pct': round(return_pct, 4),
            'strategy': 'order_block',
            'idx': ob_idx,
            'ob_level': round(ob_level, 4),
            'displacement_idx': i,
        }
        signals.append(signal)

    return signals
