"""
Regime filters для торговой стратегии.

Содержит:
    calc_adx()          — ADX (Average Directional Index) на close-only данных
    calc_roc()          — Rate of Change (ROC) momentum
    add_regime_filter() — фильтрация сигналов по ADX
    compute_stats()     — статистика сигналов (WR%, PF, DD)
"""

from typing import List, Dict


# ── helpers ──────────────────────────────────────────────────────────────────


def _wilder_smooth(vals: List[float], period: int) -> List[float]:
    """
    Wilder's smoothing (modified moving average).

    - Первое значение: SMA по первым `period` элементам.
    - Последующие: prev - prev/period + val/period.

    Возвращает список той же длины; первые (period-1) значений = 0.0.
    """
    out: List[float] = [0.0] * len(vals)
    if len(vals) < period:
        return out

    # Первое значение — SMA первой period элементов
    first_sma = sum(vals[:period]) / period
    out[period - 1] = first_sma

    for i in range(period, len(vals)):
        out[i] = out[i - 1] - out[i - 1] / period + vals[i] / period

    return out


# ── ADX ──────────────────────────────────────────────────────────────────────


def calc_adx(close_prices: List[float], period: int = 14) -> List[float]:
    """
    Рассчитать ADX (Average Directional Index) на основе цен закрытия.

    Поскольку нет данных High/Low, True Range и Directional Movement
    аппроксимируются через изменения close → close:
        TR  = abs(close[i] - close[i-1])
        +DM = max(close[i] - close[i-1], 0)
        -DM = max(close[i-1] - close[i], 0)

    Затем:
        1. ATR = Wilder's smooth(TR, period)
        2. +DI = 100 × Wilder's smooth(+DM, period) / ATR
        3. -DI = 100 × Wilder's smooth(-DM, period) / ATR
        4.  DX = 100 × abs(+DI - -DI) / (+DI + -DI)
        5. ADX = Wilder's smooth(DX, period)

    Параметры
    ---------
    close_prices : List[float]
        Цены закрытия, упорядоченные по времени (от прошлого к настоящему).
    period : int
        Период ADX (по умолчанию 14).

    Возвращает
    ----------
    List[float]
        Список ADX той же длины, что и close_prices.
        Первые period*2+1 значений = 0.0 (недостаточно данных).
        НЕТ look-ahead: ADX[i] использует только close[:i+1].
    """
    n = len(close_prices)
    adx_out: List[float] = [0.0] * n
    if n < period * 2 + 1:
        return adx_out

    # True Range, +DM, -DM (close-only approximation)
    tr: List[float] = [0.0] * n
    plus_dm: List[float] = [0.0] * n
    minus_dm: List[float] = [0.0] * n

    for i in range(1, n):
        change = close_prices[i] - close_prices[i - 1]
        tr[i] = abs(change)
        if change > 0:
            plus_dm[i] = change
        else:
            minus_dm[i] = -change

    # Wilder's smoothing
    atr = _wilder_smooth(tr, period)
    s_plus_dm = _wilder_smooth(plus_dm, period)
    s_minus_dm = _wilder_smooth(minus_dm, period)

    # +DI, -DI, DX, ADX
    dx_list: List[float] = [0.0] * n
    for i in range(period - 1, n):
        denom = s_plus_dm[i] + s_minus_dm[i]
        if denom > 0 and atr[i] > 0:
            plus_di = 100.0 * s_plus_dm[i] / atr[i]
            minus_di = 100.0 * s_minus_dm[i] / atr[i]
            di_sum = plus_di + minus_di
            if di_sum > 0:
                dx_list[i] = 100.0 * abs(plus_di - minus_di) / di_sum

    # ADX = Wilder's smooth(DX, period)
    adx_values = _wilder_smooth(dx_list, period)

    # Копируем, оставляя первые period*2+1 значений = 0.0
    for i in range(period * 2 + 1, n):
        adx_out[i] = round(adx_values[i], 4)

    return adx_out


# ── ROC ──────────────────────────────────────────────────────────────────────


def calc_roc(vals: List[float], period: int = 5) -> List[float]:
    """
    Rate of Change (ROC) momentum.

        ROC[i] = (vals[i] - vals[i-period]) / abs(vals[i-period])

    если vals[i-period] != 0, иначе 0.0.

    Параметры
    ---------
    vals : List[float]
        Исходный ряд значений.
    period : int
        Период ROC (по умолчанию 5).

    Возвращает
    ----------
    List[float]
        Список ROC той же длины; первые `period` значений = 0.0.
    """
    out: List[float] = [0.0] * len(vals)
    for i in range(period, len(vals)):
        prev = vals[i - period]
        if prev != 0:
            out[i] = (vals[i] - prev) / abs(prev)
    return out


# ── regime filter ────────────────────────────────────────────────────────────


def add_regime_filter(
    signals: List[Dict],
    close_prices: List[float],
    signal_indices: List[int],
    adx_threshold: int = 20,
) -> List[Dict]:
    """
    Отфильтровать сигналы, оставив только те, где ADX > adx_threshold.

    ADX > 20 = трендовый режим (сигналы работают лучше).
    ADX < 20 = боковик (шум — сигналы отбрасываются).

    Параметры
    ---------
    signals : List[Dict]
        Список сигналов из detect_signals (каждый с полем 'idx').
    close_prices : List[float]
        Цены закрытия для расчёта ADX.
    signal_indices : List[int]
        Индексы каждого сигнала в close_prices.
    adx_threshold : int
        Порог ADX (по умолчанию 20).

    Возвращает
    ----------
    List[Dict]
        Отфильтрованный список сигналов.
    """
    if not signals:
        return []

    adx = calc_adx(close_prices)
    filtered: List[Dict] = []

    for sig, idx in zip(signals, signal_indices):
        if idx < len(adx) and adx[idx] > adx_threshold:
            filtered.append(sig)

    return filtered


# ── statistics ────────────────────────────────────────────────────────────────


def compute_stats(signals: List[Dict]) -> Dict[str, float]:
    """
    Вычислить статистику по списку сигналов.

    Возвращает:
        n       — количество сигналов
        wr      — winrate (%) = доля сигналов с return_pct > 0
        pf      — profit factor = sum(wins) / abs(sum(losses))
        dd      — max drawdown (%) от пика до впадины equity
        avg_ret — средняя доходность (%)
    """
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0, 'avg_ret': 0.0}

    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    wr = len(wins) / n * 100.0 if n > 0 else 0.0

    sum_wins = sum(wins) if wins else 0.0
    sum_losses = abs(sum(losses)) if losses else 0.0
    pf = sum_wins / sum_losses if sum_losses > 0 else (
        sum_wins if sum_wins > 0 else 0.0
    )

    # Max drawdown по equity curve
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd:
            max_dd = dd

    avg_ret = sum(returns) / n if n > 0 else 0.0

    return {
        'n': n,
        'wr': round(wr, 1),
        'pf': round(pf, 2),
        'dd': round(max_dd, 1),
        'avg_ret': round(avg_ret, 2),
    }
