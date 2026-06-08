"""Forex adapter — заглушка. TODO: загружать OHLCV из TQA-FOREX базы."""


def load_bars(symbol, days=30, tf='5m'):
    """TODO: реализовать загрузку forex-данных."""
    return []


def load_bars_with_oi(symbol, days=30):
    """Для форекса OI нет. Возвращает пустой список."""
    return []
