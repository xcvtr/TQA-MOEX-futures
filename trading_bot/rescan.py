#!/usr/bin/env python3
"""
Rescan BM, CC, RN с перебором vol_z, div_z, horizon порогов.

Сравнивает результаты с regime_filter (ADX) и без него.

Запуск:
    python -m trading_bot.rescan

Зависимости:
    pip install psycopg2-binary
"""

import sys
from typing import List, Dict, Tuple

from . import DEFAULT_CONFIG, StrategyConfig
from .engine import detect_signals
from .scanner import load_data
from .filters import add_regime_filter, compute_stats


# ─── конфигурация рескана ─────────────────────────────────────────────────────

SCAN_TICKERS = ['BM', 'CC', 'RN']

VOL_THRESHOLDS = [1.5, 2.0, 2.5, 3.0]
DIV_THRESHOLDS = [1.0, 1.5, 2.0]
HORIZONS = [3, 6, 12, 24]

DATA_DAYS = 365  # глубина загрузки данных


# ─── тестирование ─────────────────────────────────────────────────────────────


def _run_test(
    ticker: str,
    rows,
    vol_thresh: float,
    div_thresh: float,
    horizon: int,
    use_adx: bool,
    close_prices: List[float],
) -> Dict[str, float]:
    """
    Запустить detect_signals с заданными параметрами и вернуть статистику.

    Parameters
    ----------
    ticker : str
        Код тикера.
    rows : List[Row]
        Данные из БД.
    vol_thresh : float
        Порог z-score объёма.
    div_thresh : float
        Порог расхождения fiz/yur.
    horizon : int
        Горизонт выхода (свечи).
    use_adx : bool
        Применять ли ADX-фильтр (regime_filter).
    close_prices : List[float]
        Цены закрытия для ADX.

    Returns
    -------
    Dict[str, float]
        Статистика: n, wr, pf, dd.
    """
    cfg: StrategyConfig = {
        'vol_thresh': vol_thresh,
        'div_thresh': div_thresh,
        'horizon': horizon,
        'strategy': 'vol_surge',
    }

    signals = detect_signals(rows, cfg)

    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0}

    if use_adx:
        indices = [s['idx'] for s in signals]
        signals = add_regime_filter(signals, close_prices, indices)

    return compute_stats(signals)


def _format_row(
    ticker: str,
    vol_thresh: float,
    div_thresh: float,
    horizon: int,
    stats_no_adx: Dict[str, float],
    stats_adx: Dict[str, float],
) -> str:
    """
    Форматировать строку сравнительной таблицы.
    """
    def fmt(s: Dict[str, float]) -> str:
        return f"{s['n']}/{s['wr']}/{s['pf']}/{s['dd']}"

    return (
        f"{ticker:6s} | {vol_thresh:.1f}   | {div_thresh:.1f}   | "
        f"{horizon:2d} | {fmt(stats_no_adx):20s} | {fmt(stats_adx):20s}"
    )


# ─── main ─────────────────────────────────────────────────────────────────────


def rescan() -> None:
    """
    Основной цикл рескана.

    Для каждого тикера BM/CC/RN загружает данные,
    перебирает все комбинации vol_thresh/div_thresh/horizon,
    запускает detect_signals с ADX и без ADX,
    выводит сравнительную таблицу.
    """
    header = (
        f"{'Тикер':6s} | {'vol_z':5s} | {'div_z':5s} | {'h':2s} | "
        f"{'Без ADX: n/WR%/PF/DD':20s} | {'С ADX: n/WR%/PF/DD':20s}"
    )
    sep = "-" * len(header)

    print(header)
    print(sep)

    for ticker in SCAN_TICKERS:
        # Загружаем данные один раз на тикер
        try:
            rows = load_data(ticker, days=DATA_DAYS)
        except Exception as exc:
            print(f"[ERROR] Failed to load data for {ticker}: {exc}", file=sys.stderr)
            continue

        if len(rows) < 50:
            print(f"[WARN] {ticker}: only {len(rows)} rows, skipping", file=sys.stderr)
            continue

        close_prices = [r[5] for r in rows]  # close — 5-й элемент Row

        for vol_thresh in VOL_THRESHOLDS:
            for div_thresh in DIV_THRESHOLDS:
                for horizon in HORIZONS:
                    stats_no_adx = _run_test(
                        ticker, rows, vol_thresh, div_thresh, horizon,
                        use_adx=False, close_prices=close_prices,
                    )
                    stats_adx = _run_test(
                        ticker, rows, vol_thresh, div_thresh, horizon,
                        use_adx=True, close_prices=close_prices,
                    )

                    line = _format_row(
                        ticker, vol_thresh, div_thresh, horizon,
                        stats_no_adx, stats_adx,
                    )
                    print(line)

        # Разделитель между тикерами
        print(sep)


if __name__ == '__main__':
    rescan()
