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
    'limit_lookback': 5,
}


SCAN_SYMBOLS = [
    'HS', 'KC', 'DX', 'HY', 'BM',
]

# ─── Mean Reversion After Volatility Exhaustion ──────────────────────────────
DEFAULT_REVERSION_CONFIG = {
    'mid_low': 0.3,
    'mid_high': 0.7,
    'horizon': 12,
    'vol_thresh': 1.5,
    'range_mul': 1.5,
    'lookback_bars': 3,
    'limit_lookback': 5,
}

REVERSION_TICKERS: dict = {
    'NM': {'enabled': True, 'go': 1405, 'tick_rub': 1.0, 'minstep': 1, 'label': 'NM (фьючерс Reversion)', 'max_loss': -5.0},
    'AF': {'enabled': True, 'go': 7000, 'tick_rub': 0.74, 'minstep': 1, 'label': 'AF (Africa Reversion)', 'max_loss': -5.0},
}

# ─── Order Blocks (Variant D — Limit at OB Level) ──────────────────────────
DEFAULT_OB_CONFIG = {
    'body_mul': 1.5,
    'range_mul': 1.2,
    'horizon': 2,
    'lookback': 20,
    'limit_lookback': 5,
    'max_signal_age': 6,
    'min_history': 100,
}

OB_TICKERS: dict = {
    # Core portfolio (DD <5%)
    'UC': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'UC (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'ED': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'ED (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'Si (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'RM': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'RM (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'KC': {'enabled': True, 'go': 2500, 'tick_rub': 80.0, 'minstep': 0.01,
           'label': 'KC (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'NA': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'NA (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'GD': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'GD (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    # Expansion tier (DD 5-10%, half weight)
    'RI': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 1,
           'label': 'RI (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'LK': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'LK (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'SBERF': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 1,
              'label': 'SBERF (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'GK': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'GK (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'MC': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'MC (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'RN': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'RN (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'IMOEXF': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
               'label': 'IMOEXF (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'YD': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'YD (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
}

# ─── VWAP Deviation Reversion ──────────────────────────────────────────────
DEFAULT_VWAP_CONFIG = {
    'dev_thresh': 2.0,
    'horizon': 12,
    'vwap_window': 20,
    'atr_period': 14,
    'limit_lookback': 5,
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

# ─── OI Divergence ──────────────────────────────────────────────────────────
DEFAULT_OI_DIVERGENCE_CONFIG = {
    'lookback': 20,
    'extreme_window': 10,
    'bear_threshold': 0.95,
    'bull_threshold': 1.05,
    'horizon': 6,
    'limit_lookback': 5,
}

OI_DIVERGENCE_TICKERS: dict = {
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (RTS OI Div)', 'horizon': 6, 'max_loss': -5.0},
    'GL': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'GL (GOLD OI Div)', 'horizon': 6, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (USD/RUB OI Div)', 'horizon': 6, 'max_loss': -5.0},
}

DB_CREDENTIALS = {
    'host': '10.0.0.64',
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
        'max_loss': -5.0,
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
        'max_loss': -5.0,
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
        'max_loss': -5.0,
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
        'max_loss': -5.0,
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
        'max_loss': -5.0,
    },
}
