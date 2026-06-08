#!/usr/bin/env python3
"""Paper trading tracker — positions, trades log, PnL, exit checks."""

import json
import math
import os
import csv
from datetime import datetime, timedelta
from typing import Any, Optional

from . import CAPITAL, MARGIN_USAGE, TICKERS, REVERSION_TICKERS, OB_TICKERS, VWAP_TICKERS, OI_DIVERGENCE_TICKERS

ALL_TICKERS = {**TICKERS, **REVERSION_TICKERS, **OB_TICKERS, **VWAP_TICKERS, **OI_DIVERGENCE_TICKERS}

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), 'positions.json')
TRADES_LOG = os.path.join(os.path.dirname(__file__), 'trades.csv')

# ─── helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat(sep='T', timespec='seconds')


def _ticker_config(symbol: str) -> dict:
    cfg = ALL_TICKERS.get(symbol)
    if cfg is None:
        raise ValueError(f"Unknown ticker symbol: {symbol}")
    return cfg


def _calc_contracts(symbol: str) -> int:
    """Рассчитать количество контрактов: floor(капитал × MARGIN_USAGE / ГО)."""
    cfg = _ticker_config(symbol)
    go = cfg['go']
    max_risk = CAPITAL * MARGIN_USAGE
    contracts = math.floor(max_risk / go)
    return max(contracts, 1)


def _calc_pnl(direction: str, entry: float, exit_price: float,
              contracts: int, symbol: str) -> float:
    """Рассчитать PnL в рублях для LONG / SHORT."""
    cfg = _ticker_config(symbol)
    minstep = cfg['minstep']
    tick_rub = cfg['tick_rub']
    moves = (exit_price - entry) / minstep
    if direction.upper() == 'SHORT':
        moves = -moves
    return round(moves * tick_rub * contracts, 2)


def _pnl_pct(direction: str, entry: float, exit_price: float) -> float:
    """Процент изменения цены."""
    if direction.upper() == 'LONG':
        return round((exit_price - entry) / entry * 100, 2)
    else:
        return round((entry - exit_price) / entry * 100, 2)


def _make_position_id(symbol: str, time_str: Optional[str] = None) -> str:
    """Сгенерировать ID позиции: SYMBOL_YYYYMMDD_HHMM."""
    if time_str is None:
        dt = datetime.now()
    else:
        dt = datetime.fromisoformat(time_str)
    return f"{symbol}_{dt.strftime('%Y%m%d_%H%M')}"


# ─── positions.json I/O ────────────────────────────────────────────

def load_positions() -> list[dict]:
    """Загрузить открытые позиции из JSON."""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('positions', [])
    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        return []


def save_positions(positions: list[dict]) -> None:
    """Сохранить список позиций в JSON."""
    with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'positions': positions}, f, indent=2, ensure_ascii=False)


# ─── trades.csv I/O ─────────────────────────────────────────────────

def _ensure_trades_header() -> None:
    """Создать CSV с заголовками, если файла нет."""
    if not os.path.exists(TRADES_LOG):
        with open(TRADES_LOG, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'time', 'symbol', 'direction', 'entry', 'exit',
                'contracts', 'pnl_pct', 'pnl_rub', 'status'
            ])


def _append_trade(row: list) -> None:
    """Добавить строку в trades.csv."""
    _ensure_trades_header()
    with open(TRADES_LOG, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(row)


# ─── open / close ───────────────────────────────────────────────────

def open_position(
    symbol: str,
    direction: str,
    entry_price: float,
    contracts: Optional[int] = None,
    signal_time: Optional[str] = None,
    horizon: Optional[int] = None,
) -> dict:
    """
    Открыть бумажную позицию.

    Parameters
    ----------
    symbol : str
        Тикер (HS, KC, DX, HY).
    direction : str
        'LONG' или 'SHORT'.
    entry_price : float
        Цена входа.
    contracts : int, optional
        Количество контрактов. Если None — рассчитывается автоматически
        из CAPITAL × MARGIN_USAGE / go.
    signal_time : str, optional
        Время сигнала (ISO). Если None — текущее время.
    horizon : int, optional
        Горизонт удержания в барах 5m. Если None — из конфига тикера.

    Returns
    -------
    dict
        Словарь открытой позиции.
    """
    if contracts is None:
        contracts = _calc_contracts(symbol)
    if signal_time is None:
        signal_time = _now_iso()
    if horizon is None:
        horizon = _ticker_config(symbol).get('horizon', 12)

    pos_id = _make_position_id(symbol, signal_time)

    position = {
        'id': pos_id,
        'symbol': symbol,
        'direction': direction.upper(),
        'entry_price': entry_price,
        'contracts': contracts,
        'entry_time': signal_time,
        'horizon': horizon,
        'bars_held': 0,
        'status': 'open',
        'pnl': None,
    }

    positions = load_positions()
    positions.append(position)
    save_positions(positions)

    # Запись в CSV
    _append_trade([
        signal_time, symbol, direction.upper(), entry_price, '',
        contracts, '', '', 'open'
    ])

    return position


def close_position(
    position_id: str,
    exit_price: float,
    exit_time: Optional[str] = None,
    reason: str = 'manual',
) -> Optional[dict]:
    """
    Закрыть позицию. Посчитать PnL. Обновить trades.csv.

    Parameters
    ----------
    position_id : str
        ID позиции (например 'HS_20260608_1430').
    exit_price : float
        Цена закрытия.
    exit_time : str, optional
        Время закрытия (ISO). Если None — текущее.
    reason : str
        Причина закрытия: 'manual', 'horizon', 'stop'.

    Returns
    -------
    dict or None
        Обновлённая позиция с PnL, или None если позиция не найдена.
    """
    if exit_time is None:
        exit_time = _now_iso()

    positions = load_positions()
    found = None
    for pos in positions:
        if pos['id'] == position_id and pos['status'] == 'open':
            found = pos
            break

    if found is None:
        return None

    # Расчёт PnL
    pnl_rub = _calc_pnl(
        found['direction'], found['entry_price'], exit_price,
        found['contracts'], found['symbol']
    )
    pnl_pct = _pnl_pct(found['direction'], found['entry_price'], exit_price)

    found['exit_price'] = exit_price
    found['exit_time'] = exit_time
    found['pnl'] = pnl_rub
    found['pnl_pct'] = pnl_pct
    found['status'] = 'closed'
    found['close_reason'] = reason

    save_positions(positions)

    # Дописать строку в CSV
    _append_trade([
        found['entry_time'], found['symbol'], found['direction'],
        found['entry_price'], exit_price, found['contracts'],
        pnl_pct, pnl_rub, f'closed_{reason}'
    ])

    return found


# ─── exit checks ────────────────────────────────────────────────────

def check_exits(active_signals: list[dict]) -> list[dict]:
    """
    Проверить, какие открытые позиции должны закрыться.

    Criteria:
    - По горизонту: bars_held >= horizon (прошло N баров 5m)
    - По стопу: убыток > max_loss %
    - По сигналу: если в active_signals нет сигнала по этому тикеру

    Parameters
    ----------
    active_signals : list[dict]
        Список активных сигналов с полями 'symbol' и 'direction'.

    Returns
    -------
    list[dict]
        Список закрытых позиций (каждая с PnL).
    """
    closed: list[dict] = []

    positions = load_positions()
    now_iso = _now_iso()

    # Текущие цены для стопа берём из последних сигналов
    current_prices: dict[str, float] = {}
    for sig in active_signals:
        price = sig.get('close') or sig.get('price') or sig.get('entry_price')
        if price is not None:
            current_prices[sig['symbol']] = price

    active_symbols = {sig['symbol'] for sig in active_signals}

    updated = []
    for pos in positions:
        if pos['status'] != 'open':
            updated.append(pos)
            continue

        pos['bars_held'] = pos.get('bars_held', 0) + 1
        should_close = False
        reason = ''

        # 1. Горизонт
        if pos['bars_held'] >= pos['horizon']:
            should_close = True
            reason = 'horizon'

        # 2. Стоп-лосс (по текущей цене из сигнала)
        if not should_close:
            cur_price = current_prices.get(pos['symbol'])
            if cur_price is not None:
                pnl_pct_est = _pnl_pct(
                    pos['direction'], pos['entry_price'], cur_price
                )
                max_loss = _ticker_config(pos['symbol']).get('max_loss', -5.0)
                if pnl_pct_est <= max_loss:
                    should_close = True
                    reason = 'stop'
                    pos['exit_price'] = cur_price  # type: ignore[assignment]

        # 3. Пропал сигнал
        if not should_close and pos['symbol'] not in active_symbols:
            should_close = True
            reason = 'signal_lost'

        if should_close:
            exit_price = pos.get('exit_price') or current_prices.get(
                pos['symbol'], pos['entry_price']
            )
            closed_pos = close_position(
                pos['id'], exit_price, now_iso, reason
            )
            if closed_pos:
                closed.append(closed_pos)
        else:
            updated.append(pos)

    # Сохраняем обновлённые bars_held для открытых позиций
    save_positions(updated)

    return closed


# ─── statistics ─────────────────────────────────────────────────────

def get_stats() -> dict[str, Any]:
    """
    Вернуть статистику: всего сделок, WR, PnL, equity curve.

    Returns
    -------
    dict
        {
            'total_trades': int,
            'won': int,
            'lost': int,
            'winrate': float,
            'total_pnl': float,
            'avg_pnl': float,
            'max_pnl': float,
            'max_loss': float,
            'equity_curve': list[float],
            'open_positions': int,
        }
    """
    positions = load_positions()

    closed_trades = [p for p in positions if p['status'] == 'closed']
    open_positions = [p for p in positions if p['status'] == 'open']

    total = len(closed_trades)
    if total == 0:
        return {
            'total_trades': 0,
            'won': 0,
            'lost': 0,
            'winrate': 0.0,
            'total_pnl': 0.0,
            'avg_pnl': 0.0,
            'max_pnl': 0.0,
            'max_loss': 0.0,
            'equity_curve': [],
            'open_positions': len(open_positions),
        }

    won = [p for p in closed_trades if (p.get('pnl') or 0) > 0]
    lost = [p for p in closed_trades if (p.get('pnl') or 0) <= 0]

    pnls = [p.get('pnl', 0) for p in closed_trades]
    total_pnl = round(sum(pnls), 2)
    avg_pnl = round(total_pnl / total, 2) if total else 0.0
    max_pnl = round(max(pnls), 2)
    max_loss = round(min(pnls), 2)

    # Equity curve — накопленная сумма PnL
    equity = [0.0]
    for pnl in pnls:
        equity.append(round(equity[-1] + pnl, 2))
    equity = equity[1:]  # убираем начальный 0

    return {
        'total_trades': total,
        'won': len(won),
        'lost': len(lost),
        'winrate': round(len(won) / total * 100, 2) if total else 0.0,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
        'max_pnl': max_pnl,
        'max_loss': max_loss,
        'equity_curve': equity,
        'open_positions': len(open_positions),
    }


# ─── convenience ───────────────────────────────────────────────────

def reset_all() -> None:
    """Сбросить все позиции и трейды (danger: удаляет данные)."""
    if os.path.exists(POSITIONS_FILE):
        os.remove(POSITIONS_FILE)
    if os.path.exists(TRADES_LOG):
        os.remove(TRADES_LOG)
