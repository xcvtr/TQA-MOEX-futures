"""
Trading Bot — ядро системы сигналов на основе MOEX данных.

Пакет содержит:
- engine.py: Z-Score Engine для расчёта сигналов
- scanner.py: Сканирование тикеров и алерты
"""

from typing import TypedDict


class StrategyConfig(TypedDict, total=False):
    """Конфигурация стратегии для detect_signals."""
    vol_thresh: float      # Порог z-score объёма
    div_thresh: float      # Порог расхождения fiz/yur z-score
    horizon: int           # Горизонт выхода в свечах
    strategy: str          # 'vol_surge' | 'yur_dom'
    yur_dom_ratio: float   # Множитель доминирования юрлиц (для yur_dom)


DEFAULT_CONFIG: StrategyConfig = {
    'vol_thresh': 2.0,
    'div_thresh': 1.5,
    'horizon': 6,
    'strategy': 'vol_surge',
    'yur_dom_ratio': 1.5,
}


SCAN_SYMBOLS = [
    'HS', 'KC', 'DX', 'HY', 'BM',
]

DB_CREDENTIALS = {
    'host': '10.0.0.60',
    'port': 5432,
    'dbname': 'moex',
    'user': 'postgres',
    'password': 'postgres',
}

# ─── Paper trading ──────────────────────────────────────────────────
CAPITAL = 300_000          # руб — стартовый капитал
MARGIN_USAGE = 0.5         # 50% ГО используем

TICKERS: dict = {
    'HS': {
        'label': 'HS (фьючерс)',
        'enabled': True,
        'vol_thresh': 2.75,
        'div_thresh': 1.5,
        'horizon': 12,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 5000,
        'strategy': 'vol_surge',
        'adx_filter': True,
        'adx_threshold': 20,
    },
    'KC': {
        'label': 'KC (кофе)',
        'enabled': True,
        'vol_thresh': 2.0,
        'div_thresh': 2.0,
        'horizon': 24,
        'minstep': 0.01,
        'lot': 100,
        'tick_rub': 80.0,
        'go': 2500,
        'strategy': 'vol_surge',
        'adx_filter': False,  # ADX kills KC signals
    },
    'DX': {
        'label': 'DX (фьючерс)',
        'enabled': True,
        'vol_thresh': 3.0,
        'div_thresh': 1.5,
        'horizon': 48,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 3000,
        'strategy': 'vol_surge',
        'adx_filter': False,  # Too few signals with ADX
    },
    'HY': {
        'label': 'HY (акции)',
        'enabled': True,
        'vol_thresh': 2.5,
        'horizon': 48,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 3000,
        'strategy': 'yur_dom',
        'yur_dom_ratio': 1.5,
        'adx_filter': True,
        'adx_threshold': 20,
    },
    'BM': {
        'label': 'BM (фьючерс)',
        'enabled': True,
        'vol_thresh': 2.0,
        'div_thresh': 1.5,
        'horizon': 3,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 5000,
        'strategy': 'vol_surge',
        'adx_filter': True,
        'adx_threshold': 20,
    },
}
