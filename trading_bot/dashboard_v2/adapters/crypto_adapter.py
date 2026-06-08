"""Crypto adapter — заглушка. TODO: загружать OHLCV из TQA-crypto базы."""


def load_bars(symbol, days=30, tf='5m'):
    """TODO: реализовать загрузку крипто-данных."""
    return []


def load_bars_with_oi(symbol, days=30):
    """Для крипты OI нет. Возвращает пустой список."""
    return []
