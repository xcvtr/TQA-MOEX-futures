"""MOEX adapter — загружает OHLCV + OI из БД moex."""

from datetime import datetime, timedelta, timezone

import psycopg2

from trading_bot import DB_CREDENTIALS


def load_bars(symbol, days=30, tf='5m'):
    """Загрузить OHLCV 5m из БД. Возвращает список dict с ключами time, open, high, low, close, volume."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    conn = psycopg2.connect(**DB_CREDENTIALS)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT time, open, high, low, close, volume
            FROM moex_prices_5m
            WHERE symbol = %s AND time >= %s
            ORDER BY time
        """, (symbol, since))
        for rec in cur:
            time_str = rec[0].isoformat() if hasattr(rec[0], 'isoformat') else str(rec[0])
            rows.append({
                'time': time_str,
                'open': float(rec[1]),
                'high': float(rec[2]),
                'low': float(rec[3]),
                'close': float(rec[4]),
                'volume': float(rec[5]),
            })
        cur.close()
    finally:
        conn.close()
    return rows


def load_bars_with_oi(symbol, days=30):
    """Загрузить OHLCV + OI (5m). Возвращает список (time, fiz_buy, fiz_sell, yur_buy, yur_sell, close, volume, open)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    conn = psycopg2.connect(**DB_CREDENTIALS)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.time, oi.fiz_buy, oi.fiz_sell, oi.yur_buy, oi.yur_sell,
                   p.close, p.volume, p.open
            FROM moex_prices_5m p
            JOIN moex_prices_5m_oi oi ON p.symbol = oi.symbol AND p.time = oi.time
            WHERE p.symbol = %s AND p.time >= %s
            ORDER BY p.time
        """, (symbol, since))
        for rec in cur:
            time_str = rec[0].isoformat() if hasattr(rec[0], 'isoformat') else str(rec[0])
            rows.append((
                time_str,
                float(rec[1]),
                float(rec[2]),
                float(rec[3]),
                float(rec[4]),
                float(rec[5]),
                float(rec[6]),
                float(rec[7]),
            ))
        cur.close()
    finally:
        conn.close()
    return rows


def get_freshness():
    """Вернуть словарь {ticker: last_bar_time} для всех тикеров."""
    conn = psycopg2.connect(**DB_CREDENTIALS)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, MAX(time) as last_bar
            FROM moex_prices_5m
            GROUP BY symbol
            ORDER BY symbol
        """)
        rows = {}
        for rec in cur:
            time_str = rec[1].isoformat() if hasattr(rec[1], 'isoformat') else str(rec[1])
            rows[rec[0]] = time_str
        cur.close()
        return rows
    finally:
        conn.close()


def get_db_status():
    """Проверить подключение к БД."""
    try:
        conn = psycopg2.connect(**DB_CREDENTIALS)
        conn.close()
        return {'connected': True, 'host': DB_CREDENTIALS['host'], 'dbname': DB_CREDENTIALS['dbname']}
    except Exception as e:
        return {'connected': False, 'error': str(e)}
