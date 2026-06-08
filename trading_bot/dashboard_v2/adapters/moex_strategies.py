"""MOEX strategy registration — auto-registers all 4 strategies at import time."""

from ..core.registry import register_strategy, StrategyInfo, map_strategy_to_market

from trading_bot.engine import detect_signals as _engine_detect
from trading_bot.reversion_engine import detect_mean_reversion_signals
from trading_bot.ob_engine import detect_order_block_signals
from trading_bot.vwap_engine import detect_vwap_signals

from trading_bot import (
    TICKERS, REVERSION_TICKERS, OB_TICKERS, VWAP_TICKERS,
    DEFAULT_CONFIG, DEFAULT_REVERSION_CONFIG, DEFAULT_OB_CONFIG, DEFAULT_VWAP_CONFIG,
)

# ── 1. Volume Surge (Z-Score Engine) ──────────────────────────────────
VS_TICKER_SYMBOLS = ['HS', 'KC', 'DX', 'HY', 'BM']

vs_tickers = {}
for sym in VS_TICKER_SYMBOLS:
    if sym in TICKERS:
        c = TICKERS[sym]
        vs_tickers[sym] = {
            'go': c.get('go', 0),
            'tick_rub': c.get('tick_rub', 0),
            'minstep': c.get('minstep', 1),
            'label': c.get('label', sym),
            'strategy': c.get('strategy', 'vol_surge'),
            'vol_thresh': c.get('vol_thresh', 2.0),
            'div_thresh': c.get('div_thresh', 1.5),
            'horizon': c.get('horizon', 6),
            'yur_dom_ratio': c.get('yur_dom_ratio', 1.5),
        }


def _vs_detect(symbol, data, config=None):
    """Wrapper: конвертирует dict->tuple для engine.detect_signals (ожидает Row=(time,fiz_buy,fiz_sell,yur_buy,yur_sell,close,volume,open))."""
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    # VS engine needs OI data with specific tuple format: (time, fiz_buy, fiz_sell, yur_buy, yur_sell, close, volume, open)
    # data comes from load_bars_with_oi which returns tuples
    return _engine_detect(data, cfg)


register_strategy(StrategyInfo(
    name='volume_surge',
    display_name='Volume Surge (Z-Score)',
    detect_fn=_vs_detect,
    default_config=dict(DEFAULT_CONFIG),
    tickers=vs_tickers,
    needs_oi=True,
    description='Объёмный всплеск + расхождение fiz/yur z-score (все 5 тикеров)',
))
map_strategy_to_market('volume_surge', 'moex')


# ── 2. Mean Reversion ────────────────────────────────────────────────
reversion_tickers = {k: dict(v) for k, v in REVERSION_TICKERS.items()}


def _rev_detect(symbol, data, config=None):
    """Wrapper: конвертирует dict->tuple для reversion_engine (ожидает (time,open,high,low,close,volume))."""
    cfg = dict(DEFAULT_REVERSION_CONFIG)
    if config:
        cfg.update(config)
    tuples = [(d['time'], d['open'], d['high'], d['low'], d['close'], d['volume']) for d in data]
    return detect_mean_reversion_signals(symbol, tuples, cfg)


register_strategy(StrategyInfo(
    name='reversion',
    display_name='Mean Reversion After Volatility Exhaustion',
    detect_fn=_rev_detect,
    default_config=dict(DEFAULT_REVERSION_CONFIG),
    tickers=reversion_tickers,
    needs_oi=False,
    description='Mean Reversion после всплеска волатильности (NM, AF)',
))
map_strategy_to_market('reversion', 'moex')


# ── 3. Order Block ───────────────────────────────────────────────────
ob_tickers = {k: dict(v) for k, v in OB_TICKERS.items()}


def _ob_detect(symbol, data, config=None):
    """Wrapper: конвертирует dict->tuple для ob_engine (ожидает (time,open,high,low,close,volume))."""
    cfg = dict(DEFAULT_OB_CONFIG)
    if config:
        cfg.update(config)
    tuples = [(d['time'], d['open'], d['high'], d['low'], d['close'], d['volume']) for d in data]
    return detect_order_block_signals(symbol, tuples, cfg)


register_strategy(StrategyInfo(
    name='order_block',
    display_name='Order Block (ICT Smart Money)',
    detect_fn=_ob_detect,
    default_config=dict(DEFAULT_OB_CONFIG),
    tickers=ob_tickers,
    needs_oi=False,
    description='ICT Order Block — displacement + OB level (SBERF, BR)',
))
map_strategy_to_market('order_block', 'moex')


# ── 4. VWAP Deviation Reversion ──────────────────────────────────────
vwap_tickers = {k: dict(v) for k, v in VWAP_TICKERS.items()}


def _vwap_detect(symbol, data, config=None):
    cfg = dict(DEFAULT_VWAP_CONFIG)
    if config:
        cfg.update(config)
    return detect_vwap_signals(symbol, data, cfg)


register_strategy(StrategyInfo(
    name='vwap',
    display_name='VWAP Deviation Reversion',
    detect_fn=_vwap_detect,
    default_config=dict(DEFAULT_VWAP_CONFIG),
    tickers=vwap_tickers,
    needs_oi=False,
    description='Отклонение от VWAP > 2 ATR → реверсия (GZ, Eu, SR, Si, MC)',
))
map_strategy_to_market('vwap', 'moex')
