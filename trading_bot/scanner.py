"""
Scanner — сканирование тикеров MOEX и генерация алертов.

Функции:
    load_data(symbol, days=30)    — загрузка 5m данных из БД
    scan_all(configs)              — сканирование всех включённых тикеров
    format_signal(sig, ticker)     — форматирование сигнала для алерта
"""

from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

from . import DB_CREDENTIALS, StrategyConfig
from .engine import Row, Signal, detect_signals, zs

# ── database helpers ──────────────────────────────────────────────────────────


def _get_conn():
    """Создать и вернуть подключение к БД MOEX."""
    return psycopg2.connect(
        host=DB_CREDENTIALS['host'],
        port=DB_CREDENTIALS['port'],
        dbname=DB_CREDENTIALS['dbname'],
        user=DB_CREDENTIALS['user'],
        password=DB_CREDENTIALS['password'],
    )


# ── data loading ─────────────────────────────────────────────────────────────


def load_data(symbol: str, days: int = 30) -> List[Row]:
    """
    Загрузить 5-минутные данные для тикера из БД.

    JOIN moex_prices_5m_oi + moex_prices_5m, фильтр по symbol и days.

    Параметры
    ---------
    symbol : str
        Код тикера (например 'SBER').
    days : int
        Количество дней истории для загрузки (по умолчанию 30).

    Возвращает
    ----------
    List[Row]
        Список кортежей (time, fiz_buy, fiz_sell, yur_buy, yur_sell,
                         close, volume, open),
        упорядоченный по возрастанию времени.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = """
        SELECT
            p.time,
            oi.fiz_buy,
            oi.fiz_sell,
            oi.yur_buy,
            oi.yur_sell,
            p.close,
            p.volume,
            p.open
        FROM moex_prices_5m_oi oi
        JOIN moex_prices_5m p
            ON p.symbol = oi.symbol AND p.time = oi.time
        WHERE oi.symbol = %s
          AND oi.time >= %s
        ORDER BY oi.time ASC
    """
    rows: List[Row] = []
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, (symbol, since))
            for rec in cur:
                time_str = rec[0].isoformat() if hasattr(rec[0], 'isoformat') else str(rec[0])
                rows.append((
                    time_str,
                    float(rec[1]),   # fiz_buy
                    float(rec[2]),   # fiz_sell
                    float(rec[3]),   # yur_buy
                    float(rec[4]),   # yur_sell
                    float(rec[5]),   # close
                    float(rec[6]),   # volume
                    float(rec[7]),   # open
                ))
    finally:
        conn.close()
    return rows


# ── scanning ─────────────────────────────────────────────────────────────────


def scan_all(
    configs: Optional[Dict[str, StrategyConfig]] = None,
) -> List[Dict[str, object]]:
    """
    Просканировать все включённые тикеры и вернуть активные сигналы.

    Для каждого тикера из SCAN_SYMBOLS загружаются данные,
    вычисляются z-scores и детектируются сигналы согласно конфигурации.

    Параметры
    ---------
    configs : Dict[str, StrategyConfig], optional
        Словарь {тикер: конфиг} для переопределения параметров стратегии
        для конкретных инструментов. Если None — используется DEFAULT_CONFIG.

    Возвращает
    ----------
    List[Dict[str, object]]
        Список сигналов, обогащённых полем 'ticker'.
        Каждый элемент: {**signal, 'ticker': str}.
    """
    from . import DEFAULT_CONFIG, SCAN_SYMBOLS

    if configs is None:
        configs = {}

    all_signals: List[Dict[str, object]] = []

    for ticker in SCAN_SYMBOLS:
        cfg = configs.get(ticker, DEFAULT_CONFIG)
        try:
            rows = load_data(ticker)
        except Exception as exc:
            # Логируем ошибку, но не прерываем сканирование остальных
            print(f"[WARN] Failed to load data for {ticker}: {exc}")
            continue

        if len(rows) < 25:  # нужно хотя бы 20 + несколько для теста
            continue

        signals = detect_signals(rows, cfg)
        for sig in signals:
            enriched = dict(sig)  # type: ignore
            enriched['ticker'] = ticker
            all_signals.append(enriched)

    return all_signals


# ── formatting ───────────────────────────────────────────────────────────────


def format_signal(sig: Dict[str, object], ticker: str) -> str:
    """
    Форматировать сигнал в человекочитаемый алерт.

    Параметры
    ---------
    sig : Dict[str, object]
        Сигнал из detect_signals (поля: time, direction, entry, exit,
               return_pct, vol_z, yur_z, fiz_z).
    ticker : str
        Код тикера.

    Возвращает
    ----------
    str
        Отформатированное сообщение для отправки в Telegram / алерт.
    """
    direction = sig.get('direction', '?')
    entry = sig.get('entry', 0.0)
    exit_ = sig.get('exit', 0.0)
    ret = sig.get('return_pct', 0.0)
    vol_z = sig.get('vol_z', 0.0)
    yur_z = sig.get('yur_z', 0.0)
    fiz_z = sig.get('fiz_z', 0.0)
    time_str = sig.get('time', '???')

    emoji = '🟢' if direction == 'LONG' else '🔴'

    lines = [
        f"{emoji} {ticker} | {direction} @ {time_str}",
        f"    Entry: {entry:.2f} → Exit: {exit_:.2f} ({ret:+.2f}%)",
        f"    vol_z={vol_z:+.2f}  yur_z={yur_z:+.2f}  fiz_z={fiz_z:+.2f}",
    ]
    return '\n'.join(lines)
