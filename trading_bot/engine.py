"""
Engine — Z-Score Engine для расчёта торговых сигналов.

Функции:
    zs(vals, w=20)         — rolling z-score без look-ahead bias
    detect_signals(...)    — обнаружение сигналов по стратегии
"""

from typing import List, Dict, Tuple, Optional

from . import StrategyConfig


# ── helpers ──────────────────────────────────────────────────────────────────


def zs(vals: List[float], w: int = 20) -> List[float]:
    """
    Rolling z-score на основе w предыдущих значений.

    Для каждого i >= w вычисляется среднее и стандартное отклонение
    по vals[i-w:i] (строго прошлые данные), после чего z-оценка
    считается как (vals[i] - mu) / sd.

    Параметры
    ---------
    vals : List[float]
        Исходный ряд значений.
    w : int
        Размер окна (по умолчанию 20).

    Возвращает
    ----------
    List[float]
        Список z-оценок той же длины; первые w элементов равны 0.0.
    """
    out: List[float] = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i - w:i]
        mu = sum(chunk) / w
        var = sum((x - mu) ** 2 for x in chunk) / w
        sd = var ** 0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


# ── signal detection ─────────────────────────────────────────────────────────


Row = Tuple[str, float, float, float, float, float, float, float, float, float]
"""
Формат строки данных:
    (time, fiz_buy, fiz_sell, yur_buy, yur_sell, close, volume, open, high, low)
"""

Signal = Dict[str, object]
"""
Формат сигнала:
    {
        'time':       str,    # время свечи-триггера
        'direction':  str,    # 'LONG' | 'SHORT'
        'entry':      float,  # цена входа
        'exit':       float,  # цена выхода (close[i+horizon])
        'return_pct': float,  # доходность в процентах
        'vol_z':      float,  # z-score объёма в момент сигнала
        'yur_z':      float,  # z-score активности юрлиц
        'fiz_z':      float,  # z-score активности физлиц
        'idx':        int,    # индекс сигнала в исходном массиве rows
        # limit-специфичные:
        'fill_bar':   int,    # индекс бара, на котором заполнился лимитник
        'limit_price': float, # цена лимитного ордера
    }
"""


def _compute_z_scores(rows: List[Row], w: int) -> Tuple[
    List[float], List[float], List[float], List[float], List[float]
]:
    """
    Вычислить все необходимые z-scores на основе rows.

    Возвращает (vol_z, fiz_z, yur_z, fiz_vol_list, yur_vol_list).
    """
    volumes = [r[6] for r in rows]
    fiz_buy  = [r[1] for r in rows]
    fiz_sell = [r[2] for r in rows]
    yur_buy  = [r[3] for r in rows]
    yur_sell = [r[4] for r in rows]

    fiz_total = [fiz_buy[i] + fiz_sell[i] for i in range(len(rows))]
    yur_total = [yur_buy[i] + yur_sell[i] for i in range(len(rows))]

    vol_z = zs(volumes, w)
    fiz_z = zs(fiz_total, w)
    yur_z = zs(yur_total, w)

    return vol_z, fiz_z, yur_z, fiz_total, yur_total


def detect_signals(
    rows: List[Row],
    config: Optional[StrategyConfig] = None,
) -> List[Signal]:
    """
    Обнаружить торговые сигналы на основе Z-Score анализа.

    Параметры
    ---------
    rows : List[Row]
        Список кортежей (time, fiz_buy, fiz_sell, yur_buy, yur_sell,
                          close, volume, open).
        Упорядочен по возрастанию времени — от прошлого к настоящему.
    config : StrategyConfig, optional
        Конфигурация стратегии. Если None — используется DEFAULT_CONFIG.

    Возвращает
    ----------
    List[Signal]
        Список обнаруженных сигналов, отсортированных по времени.
    """
    if config is None:
        from . import DEFAULT_CONFIG
        cfg = dict(DEFAULT_CONFIG)  # type: ignore
    else:
        cfg = dict(config)  # type: ignore

    strategy   = cfg.get('strategy', 'vol_surge')
    vol_thresh = cfg.get('vol_thresh', 2.0)
    div_thresh = cfg.get('div_thresh', 1.5)
    horizon    = cfg.get('horizon', 6)
    yur_dom_ratio = cfg.get('yur_dom_ratio', 1.5)

    w = 20  # окно для z-score
    vol_z, fiz_z, yur_z, fiz_vol, yur_vol = _compute_z_scores(rows, w)

    n = len(rows)
    signals: List[Signal] = []

    for i in range(w, n):
        # Проверяем, что есть данные для входа и выхода
        if i + 1 >= n:
            break  # нет open[i+1]
        if i + horizon >= n:
            continue  # нет close[i+horizon]

        if strategy == 'vol_surge':
            # Условие 1: всплеск объёма
            if vol_z[i] < vol_thresh:
                continue
            # Условие 2: обе группы активны
            if abs(fiz_z[i]) < div_thresh or abs(yur_z[i]) < div_thresh:
                continue
            # Условие 3: направления противоположны
            if fiz_z[i] * yur_z[i] >= 0:
                continue

        elif strategy == 'yur_dom':
            # Условие 1: всплеск объёма
            if vol_z[i] < vol_thresh:
                continue
            # Условие 2: юрлица активны (менее строгий порог)
            if abs(yur_z[i]) < 1.0:
                continue
            # Условие 3: объём юрлиц доминирует
            if yur_vol[i] <= fiz_vol[i] * yur_dom_ratio:
                continue
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        # Определяем направление
        direction = 'LONG' if yur_z[i] > 0 else 'SHORT'

        entry = rows[i + 1][7]       # open[i+1]
        exit_ = rows[i + horizon][5]  # close[i+horizon]

        if direction == 'LONG':
            return_pct = (exit_ - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_) / entry * 100.0

        signals.append({
            'time':       rows[i][0],
            'direction':  direction,
            'entry':      entry,
            'exit':       exit_,
            'return_pct': round(return_pct, 4),
            'vol_z':      round(vol_z[i], 4),
            'yur_z':      round(yur_z[i], 4),
            'fiz_z':      round(fiz_z[i], 4),
            'idx':        i,
        })

    return signals


def detect_signals_limit(
    rows: List[Row],
    config: Optional[StrategyConfig] = None,
) -> List[Signal]:
    """
    Limit-order variant of detect_signals.

    LONG: limit_price = low[i], fill when low[j] <= limit_price
    SHORT: limit_price = high[i], fill when high[j] >= limit_price
    Search fill within limit_lookback bars after trigger.
    """
    if config is None:
        from . import DEFAULT_CONFIG
        cfg = dict(DEFAULT_CONFIG)
    else:
        cfg = dict(config)

    strategy   = cfg.get('strategy', 'vol_surge')
    vol_thresh = cfg.get('vol_thresh', 2.0)
    div_thresh = cfg.get('div_thresh', 1.5)
    horizon    = cfg.get('horizon', 6)
    yur_dom_ratio = cfg.get('yur_dom_ratio', 1.5)
    limit_lookback = cfg.get('limit_lookback', 5)

    w = 20
    vol_z, fiz_z, yur_z, fiz_vol, yur_vol = _compute_z_scores(rows, w)

    n = len(rows)
    signals: List[Signal] = []

    for i in range(w, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        if strategy == 'vol_surge':
            if vol_z[i] < vol_thresh:
                continue
            if abs(fiz_z[i]) < div_thresh or abs(yur_z[i]) < div_thresh:
                continue
            if fiz_z[i] * yur_z[i] >= 0:
                continue
        elif strategy == 'yur_dom':
            if vol_z[i] < vol_thresh:
                continue
            if abs(yur_z[i]) < 1.0:
                continue
            if yur_vol[i] <= fiz_vol[i] * yur_dom_ratio:
                continue
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        direction = 'LONG' if yur_z[i] > 0 else 'SHORT'

        if direction == 'LONG':
            limit_price = rows[i][9]  # low[i]
        else:
            limit_price = rows[i][8]  # high[i]

        fill_bar = None
        max_j = min(i + 1 + limit_lookback, n)
        for j in range(i + 1, max_j):
            if direction == 'LONG' and rows[j][9] <= limit_price:
                fill_bar = j
                break
            elif direction == 'SHORT' and rows[j][8] >= limit_price:
                fill_bar = j
                break

        if fill_bar is None:
            continue

        ex = fill_bar + horizon
        if ex >= n:
            continue

        entry = limit_price
        exit_ = rows[ex][5]

        if direction == 'LONG':
            return_pct = (exit_ - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_) / entry * 100.0

        signals.append({
            'time':       rows[i][0],
            'direction':  direction,
            'entry':      entry,
            'exit':       exit_,
            'return_pct': round(return_pct, 4),
            'vol_z':      round(vol_z[i], 4),
            'yur_z':      round(yur_z[i], 4),
            'fiz_z':      round(fiz_z[i], 4),
            'idx':        i,
            'fill_bar':   fill_bar,
            'limit_price': limit_price,
        })

    return signals
