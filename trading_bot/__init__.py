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
    'AF': {'enabled': True, 'go': 7000, 'tick_rub': 0.74, 'minstep': 1, 'label': 'AF (Africa Reversion 15m)', 'horizon': 12, 'max_loss': -5.0, 'tf': '15m'},
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
           'label': 'UC (OB Core H2)', 'horizon': 2, 'max_loss': -5.0, 'tf': 'H2'},
    'ED': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'ED (OB Core H4)', 'horizon': 2, 'max_loss': -5.0, 'tf': 'H4'},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'Si (OB Core H2)', 'horizon': 2, 'max_loss': -5.0, 'tf': 'H2'},
    'RM': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'RM (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'KC': {'enabled': True, 'go': 2500, 'tick_rub': 80.0, 'minstep': 0.01,
           'label': 'KC (OB Core H4)', 'horizon': 2, 'max_loss': -5.0, 'tf': 'H4'},
    'NA': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'NA (OB Core)', 'horizon': 2, 'max_loss': -5.0},
    'GD': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'GD (OB Core H2)', 'horizon': 2, 'max_loss': -5.0, 'tf': 'H2'},
    # Expansion tier (DD 5-10%, half weight)
    'RI': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 1,
           'label': 'RI (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'LK': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'LK (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'SBERF': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 1,
              'label': 'SBERF (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'GK': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'GK (OB Exp H4)', 'horizon': 2, 'max_loss': -5.0, 'tf': 'H4'},
    'MC': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'MC (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'RN': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'RN (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'IMOEXF': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
               'label': 'IMOEXF (OB Exp)', 'horizon': 2, 'max_loss': -5.0},
    'YD': {'enabled': True, 'go': 2500, 'tick_rub': 1.0, 'minstep': 0.01,
           'label': 'YD (OB Exp H4)', 'horizon': 2, 'max_loss': -5.0, 'tf': 'H4'},
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
    'Eu': {'enabled': False, 'go': 973, 'tick_rub': 0.01, 'minstep': 0.01,
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

# ─── Whale Detection (OI Volume Burst) ──────────────────────────────────────
DEFAULT_WHALE_CONFIG = {
    'yur_z_thresh': 2.5,
    'horizon': 12,
    'fiz_z_max': 1.5,
}

WHALE_TICKERS: dict = {
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (Whale OI)', 'horizon': 12, 'max_loss': -5.0},
    'GL': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'GL (Whale OI)', 'horizon': 12, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (Whale OI)', 'horizon': 12, 'max_loss': -5.0},
    'NM': {'enabled': True, 'go': 1405, 'tick_rub': 1.0, 'minstep': 1, 'label': 'NM (Whale OI)', 'horizon': 12, 'max_loss': -5.0},
}

# ─── Momentum Breakout + OI Confirmation ─────────────────────────────────────
DEFAULT_MOMENTUM_CONFIG = {
    'lookback': 20,
    'horizon': 24,
    'oi_growth_min': 0.0,
    'require_yur_dom': True,
}

MOMENTUM_TICKERS: dict = {
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (Momentum OI)', 'horizon': 24, 'max_loss': -5.0},
    'GL': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'GL (Momentum OI)', 'horizon': 24, 'max_loss': -5.0},
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (Momentum OI)', 'horizon': 24, 'max_loss': -5.0},
    'NM': {'enabled': True, 'go': 1405, 'tick_rub': 1.0, 'minstep': 1, 'label': 'NM (Momentum OI)', 'horizon': 24, 'max_loss': -5.0},
}

# ─── Volume Profile / HVN ────────────────────────────────────────────────────
DEFAULT_PROFILE_CONFIG = {
    'lookback': 20,
    'vol_mult': 2.0,
    'n_buckets': 10,
    'horizon': 12,
    'hvn_touch_pct': 0.01,
}

PROFILE_TICKERS: dict = {
    'Si': {'enabled': True, 'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'Si (Profile HVN)', 'horizon': 12, 'max_loss': -5.0},
    'RI': {'enabled': True, 'go': 5000, 'tick_rub': 1.0, 'minstep': 1, 'label': 'RI (Profile HVN)', 'horizon': 12, 'max_loss': -5.0},
    'NM': {'enabled': True, 'go': 1405, 'tick_rub': 1.0, 'minstep': 1, 'label': 'NM (Profile HVN)', 'horizon': 12, 'max_loss': -5.0},
    'SR': {'enabled': True, 'go': 5719, 'tick_rub': 1.0, 'minstep': 0.01, 'label': 'SR (Profile HVN)', 'horizon': 12, 'max_loss': -5.0},
    'GZ': {'enabled': True, 'go': 2065, 'tick_rub': 0.01, 'minstep': 0.01, 'label': 'GZ (Profile HVN)', 'horizon': 12, 'max_loss': -5.0},
}

# ─── Spread Trading (Pairs) ──────────────────────────────────────────────────
DEFAULT_SPREAD_CONFIG = {
    'entry_z': 2.0,
    'exit_z': 0.5,
    'horizon': 12,
    'lookback': 60,
}

SPREAD_TICKERS: dict = {
    'Si/BR': {'enabled': True, 'go_pair': 2000, 'tick_rub': 1.0, 'minstep': 0.0001, 'label': 'Si/BR (Spread)', 'horizon': 12, 'max_loss': -5.0},
    'RI/GL': {'enabled': True, 'go_pair': 5000, 'tick_rub': 1.0, 'minstep': 0.0001, 'label': 'RI/GL (Spread)', 'horizon': 12, 'max_loss': -5.0},
    'NM/AF': {'enabled': True, 'go_pair': 4000, 'tick_rub': 1.0, 'minstep': 0.0001, 'label': 'NM/AF (Spread)', 'horizon': 12, 'max_loss': -5.0},
}

# ─── Additional tickers for spread pairs ─────────────────────────────────────
SPREAD_COMPONENT_TICKERS: dict = {
    'BR': {'go': 3000, 'tick_rub': 1.0, 'minstep': 0.01},
    'AF': {'go': 7000, 'tick_rub': 0.74, 'minstep': 1},
    'Si': {'go': 1000, 'tick_rub': 1.0, 'minstep': 0.01},
    'RI': {'go': 5000, 'tick_rub': 1.0, 'minstep': 1},
    'GL': {'go': 5000, 'tick_rub': 1.0, 'minstep': 0.01},
    'NM': {'go': 1405, 'tick_rub': 1.0, 'minstep': 1},
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
MARGIN_USAGE = 0.08        # 8% на сделку (Pareto-оптимум, быль 0.5)
MAX_CONCURRENT = 3         # макс одновременных позиций
MAX_TOTAL_MARGIN = 0.2     # макс 20% капитала в ГО суммарно
STOP_LOSS_PCT = 0.02       # 2% стоп-лосс на сделку
MAX_DD_LIMIT = 0.05        # остановка торговли при просадке 5%

TICKERS: dict = {
    'HS': {
        'label': 'HS (фьючерс)',
        'enabled': True,
        'vol_thresh': 2.5,
        'div_thresh': 1.5,
        'horizon': 12,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 5000,
        'strategy': 'vol_surge',
        'tf': '5m',
        'adx_filter': True,
        'adx_threshold': 20,
        'max_loss': -5.0,
    },
    'KC': {
        'label': 'KC (кофе)',
        'enabled': False,  # WR<52% even on best TF
        'vol_thresh': 2.0,
        'div_thresh': 2.0,
        'horizon': 24,
        'minstep': 0.01,
        'lot': 100,
        'tick_rub': 80.0,
        'go': 2500,
        'strategy': 'vol_surge',
        'tf': '5m',
        'adx_filter': False,
        'max_loss': -5.0,
    },
    'DX': {
        'label': 'DX (фьючерс)',
        'enabled': True,
        'vol_thresh': 3.0,
        'horizon': 24,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 3000,
        'strategy': 'vol_surge',
        'tf': '15m',
        'adx_filter': False,
        'max_loss': -5.0,
    },
    'HY': {
        'label': 'HY (акции)',
        'enabled': True,
        'vol_thresh': 2.0,
        'horizon': 12,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 3000,
        'strategy': 'yur_dom',
        'tf': 'H1',
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
        'horizon': 12,
        'minstep': 1,
        'tick_rub': 1.0,
        'go': 5000,
        'strategy': 'vol_surge',
        'tf': 'H1',
        'adx_filter': True,
        'adx_threshold': 20,
        'max_loss': -5.0,
    },
}
