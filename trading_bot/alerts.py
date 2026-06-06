#!/usr/bin/env python3
"""Telegram/SMS alerts — форматирование сигналов и позиций для отправки."""

import os
import logging
from datetime import datetime
from typing import Any, Optional

# ─── log setup ──────────────────────────────────────────────────────

_ALERTS_DIR = os.path.join(os.path.expanduser('~'), '.hermes', 'trading_bot')
os.makedirs(_ALERTS_DIR, exist_ok=True)

_ALERTS_LOG = os.path.join(_ALERTS_DIR, 'alerts.log')

_logger = logging.getLogger('trading_bot.alerts')
_handler = logging.FileHandler(_ALERTS_LOG, encoding='utf-8')
_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
_logger.addHandler(_handler)
_logger.setLevel(logging.DEBUG)

# Уровни
_LEVEL_EMOJI = {
    'info': 'ℹ️',
    'signal': '🔔',
    'open': '🟢',
    'close': '🔴',
    'pnl': '💰',
    'warning': '⚠️',
    'error': '🚨',
    'critical': '🔥',
}

# ─── public API ─────────────────────────────────────────────────────

def send_alert(message: str, level: str = 'info') -> str:
    """
    Отправить алерт.

    - Пишет в локальный лог ~/.hermes/trading_bot/alerts.log
    - Возвращает строку с алертом (Hermes отправит через send_message)

    Parameters
    ----------
    message : str
        Текст алерта.
    level : str
        Уровень: info, signal, open, close, pnl, warning, error, critical.

    Returns
    -------
    str
        Готовая строка для отправки.
    """
    emoji = _LEVEL_EMOJI.get(level, 'ℹ️')
    timestamp = datetime.now().strftime('%H:%M:%S')
    line = f"{emoji} **[{level.upper()}]** {message}"

    _logger.log(
        {
            'info': logging.INFO,
            'signal': logging.INFO,
            'open': logging.INFO,
            'close': logging.INFO,
            'pnl': logging.INFO,
            'warning': logging.WARNING,
            'error': logging.ERROR,
            'critical': logging.CRITICAL,
        }.get(level, logging.INFO),
        '%s',
        message,
    )

    return line


# ─── formatters ─────────────────────────────────────────────────────

def format_signal_alert(sig: dict[str, Any], ticker_label: str) -> str:
    """
    Форматировать сигнал для алерта.

    Пример: 🔴 LONG HS @ 24950 | vol_z=3.2 | yur_z=+2.1 | Плечо 5x | 30 контрактов

    Parameters
    ----------
    sig : dict
        Словарь сигнала с полями:
        - symbol, direction, entry_price / price / close
        - vol_z (опционально)
        - yur_z (опционально)
        - leverage (опционально)
        - contracts (опционально)
    ticker_label : str
        Человеческое название тикера (например 'HS (фьючерс)').

    Returns
    -------
    str
        Отформатированный алерт.
    """
    direction = sig.get('direction', 'LONG')
    symbol = sig.get('symbol', '?')
    price = (sig.get('entry_price') or sig.get('entry') or sig.get('price') or sig.get('close', 0))

    # Эмодзи направления
    dir_emoji = '🟢' if direction.upper() == 'LONG' else '🔴'
    dir_label = 'LONG' if direction.upper() == 'LONG' else 'SHORT'

    parts = [f"{dir_emoji} **{dir_label} {ticker_label}** @ {price}"]

    # Дополнительные метрики
    vol_z = sig.get('vol_z')
    if vol_z is not None:
        parts.append(f"vol_z={vol_z:.1f}")

    yur_z = sig.get('yur_z')
    if yur_z is not None:
        sign = '+' if yur_z >= 0 else ''
        parts.append(f"yur_z={sign}{yur_z:.1f}")

    lev = sig.get('leverage')
    if lev is not None:
        parts.append(f"Плечо {lev}x")

    contracts = sig.get('contracts')
    if contracts is not None:
        parts.append(f"{contracts} контрактов")

    return ' | '.join(parts)


def format_position_update(pos: dict[str, Any]) -> str:
    """
    Форматировать обновление позиции (открытие/закрытие/PnL).

    Пример для открытия:
    🟢 **OPEN** HS LONG · 30 контрактов @ 24950 · Горизонт 12 баров

    Пример для закрытия:
    💰 **CLOSE** HS LONG · PnL: +4500 руб (+2.35%) · Причина: horizon

    Parameters
    ----------
    pos : dict
        Словарь позиции (из tracker.py).
        Может содержать: id, symbol, direction, entry_price, exit_price,
        contracts, pnl, pnl_pct, status, horizon, close_reason.

    Returns
    -------
    str
        Отформатированный алерт.
    """
    symbol = pos.get('symbol', '?')
    direction = pos.get('direction', 'LONG')
    dir_label = 'LONG' if direction.upper() == 'LONG' else 'SHORT'
    contracts = pos.get('contracts', '?')
    entry = pos.get('entry_price', '?')

    status = pos.get('status', 'open')

    if status == 'open':
        horizon = pos.get('horizon', '?')
        return (
            f"🟢 **OPEN** {symbol} {dir_label} · "
            f"{contracts} контрактов @ {entry} · "
            f"Горизонт {horizon} баров"
        )

    if status == 'closed':
        exit_price = pos.get('exit_price', '?')
        pnl_rub = pos.get('pnl')
        pnl_pct = pos.get('pnl_pct')
        reason = pos.get('close_reason', 'manual')

        if pnl_rub is not None:
            sign = '+' if pnl_rub >= 0 else ''
            pnl_str = f"{sign}{pnl_rub:.2f} руб"
            if pnl_pct is not None:
                pnl_str += f" ({sign}{pnl_pct:.2f}%)"
        else:
            pnl_str = '? руб'

        reason_label = {
            'horizon': 'Горизонт',
            'stop': 'Стоп-лосс',
            'signal_lost': 'Сигнал пропал',
            'manual': 'Ручное',
        }.get(reason, reason)

        emoji = '💰' if (pos.get('pnl') or 0) >= 0 else '🔴'
        return (
            f"{emoji} **CLOSE** {symbol} {dir_label} · "
            f"PnL: {pnl_str} · Причина: {reason_label}"
        )

    # Fallback
    return f"ℹ️ **UPDATE** {symbol} {dir_label} · status={status}"


def format_stats(stats: dict[str, Any]) -> str:
    """
    Форматировать статистику трейдинга.

    Parameters
    ----------
    stats : dict
        Результат tracker.get_stats().

    Returns
    -------
    str
    """
    lines = ["📊 **Trading Stats**"]
    lines.append(f"│ Сделок: {stats['total_trades']}")
    if stats['total_trades'] > 0:
        lines.append(f"│ Won/Lost: {stats['won']}/{stats['lost']}")
        lines.append(f"│ WinRate: {stats['winrate']}%")
        lines.append(f"│ Total PnL: {stats['total_pnl']:+.2f} руб")
        lines.append(f"│ Avg PnL: {stats['avg_pnl']:+.2f} руб")
        lines.append(f"│ Max PnL: {stats['max_pnl']:+.2f} руб")
        lines.append(f"│ Max Loss: {stats['max_loss']:+.2f} руб")
    lines.append(f"│ Открыто позиций: {stats['open_positions']}")
    return '\n'.join(lines)
