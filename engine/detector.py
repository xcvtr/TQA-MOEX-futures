#!/usr/bin/env python3 -u
"""Детектор сигналов TQA-MOEX-futures.

Читает portfolio из PG (futures.portfolio, enabled=true) — НИКАКИХ текстовых конфигов.
Для каждого (ticker, strategy) гоняет check_signal(bar_data, ticker, params).
Новый сигнал → INSERT в futures.signals (status='new'), дедуп по UNIQUE.

Запуск: cron каждые 5 мин в торговые часы.
Движок готов к docker: только PG+CH, без локальных файлов.
"""
import os, sys, json, logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import clickhouse_connect as cc
import psycopg2
from psycopg2.extras import execute_values

CH_HOST = os.getenv('MOEX_CH_HOST', '10.0.0.60')
CH_PORT = 8123
CH_DB = 'moex'
PG = dict(host=os.getenv('MOEX_PG_HOST', '10.0.0.60'), port=5432, dbname='moex',
          user='postgres', password=os.getenv('MOEX_PG_PASSWORD', ''), connect_timeout=5)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('detector')

# ── Стратегии: регистрация движков (как STRATEGY_MAP в папере) ──
STRATEGY_MAP = {}

def _load_strategies():
    from strategies.oi.prod.engine import check_signal as oi_check
    from strategies.oi_dom.prod.engine import check_signal as oi_dom_check
    from strategies.dayofweek.prod.engine import check_signal as dow_check
    STRATEGY_MAP['oi'] = oi_check
    STRATEGY_MAP['oi_dom'] = oi_dom_check
    STRATEGY_MAP['dayofweek'] = dow_check

def pg_conn():
    return psycopg2.connect(**PG)

def load_portfolio():
    """Включённые стратегии из PG — параметры живут в params JSONB."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, strategy, params
        FROM futures.portfolio
        WHERE enabled = true
        ORDER BY ticker, strategy
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    out = []
    for ticker, strategy, params in rows:
        if isinstance(params, str):
            try: params = json.loads(params)
            except Exception: params = {}
        out.append((ticker, strategy, params or {}))
    return out

def build_bar_data(ticker, strategy=None):
    """Бары для стратегии.
    - oi: M1 из mt5_continuous (BR/NG/SV)
    - dayofweek: D1 из CH 10.0.0.63 (SBRF/SPYF, проект MOEX-stocks-1) — M1 не нужен,
      только дневные close для prev_week_return
    """
    if strategy == 'dayofweek':
        # dayofweek нужны ДНЕВНЫЕ close за 2+ недели. M1 (mt5_continuous) агрегируем
        # в дневные; если M1 нет — fallback на H1/D1 из CH 10.0.0.63.
        bd = _build_daily_from_m1(ticker)
        if bd:
            return bd
        return build_daily_data(ticker)
    return _build_m1(ticker)


def _build_daily_from_m1(ticker):
    """Дневные close из M1 mt5_continuous (агрегация по дате, 60 дней).
    История SPYF/SBRF полная (2024+) — берём 60 дней, хватает для prev_week_return."""
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    r = ch.query(f"""
        SELECT toUnixTimestamp(toDateTime(bt)), prc
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}' AND bt >= now() - INTERVAL 60 DAY
        ORDER BY bt
    """).result_rows
    ch.close()
    if not r:
        return None
    # Агрегация по дате (IRK): последний close дня, БЕЗ выходных (Сб/Вс — гэп-бары ALLFUT)
    from collections import OrderedDict
    days = OrderedDict()
    for ts, prc in r:
        d = datetime.fromtimestamp(ts).date()
        if d.weekday() >= 5:  # Сб/Вс — артефакт непрерывного контракта
            continue
        days[d] = prc
    daily = sorted(days.items())
    if len(daily) < 3:
        return None
    bars = [{'ts': int(datetime(d.year, d.month, d.day, 23, 0).timestamp()),
             'opn': c, 'hi': c, 'lo': c, 'prc': c} for d, c in daily]
    # ts последнего = сейчас (текущий день ещё формируется)
    bars[-1]['ts'] = int(datetime.now().timestamp())
    return {'prc': bars[-1]['prc'], 'hi': bars[-1]['hi'], 'lo': bars[-1]['lo'],
            'close_hist': [b['prc'] for b in bars],
            'hi_hist': [b['hi'] for b in bars], 'lo_hist': [b['lo'] for b in bars],
            'bars_list': bars, 'ts': bars[-1]['ts']}


def _build_m1(ticker):
    """M1 из mt5_continuous, fallback prices_5min."""
    ch = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    r = ch.query(f"""
        SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc, vol
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}' AND bt >= now() - INTERVAL 2 DAY
        ORDER BY bt
    """).result_rows
    if not r:
        # Fallback: prices_5min (для SBRF/SPYF)
        r = ch.query(f"""
            SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc, vol
            FROM moex.prices_5min
            WHERE ticker = '{ticker}' AND bt >= now() - INTERVAL 2 DAY
            ORDER BY bt
        """).result_rows
    ch.close()
    if not r:
        return None
    n = min(200, len(r))
    bars = [{'ts': r[-n+i][0], 'opn': r[-n+i][1], 'hi': r[-n+i][2],
             'lo': r[-n+i][3], 'prc': r[-n+i][4], 'vol': r[-n+i][5]} for i in range(n)]
    closes = [b['prc'] for b in bars]
    bd = {
        'prc': closes[-1], 'hi': bars[-1]['hi'], 'lo': bars[-1]['lo'],
        'close_hist': closes, 'hi_hist': [b['hi'] for b in bars],
        'lo_hist': [b['lo'] for b in bars], 'vol_hist': [b['vol'] for b in bars],
        'bars_list': bars,
        'ts': bars[-1]['ts'],
    }
    return bd


def build_daily_data(ticker):
    """Дневные бары dayofweek из CH 10.0.0.63 (moex.mt5_futures, MOEX-stocks-1).
    H1 → агрегируем в дневные close (последний H1 дня). ts = конец дня."""
    try:
        ch = cc.get_client(host='10.0.0.63', port=8123, database='moex',
                           username='default', password='')
        r = ch.query(f"""
            SELECT toUnixTimestamp(toDateTime(bt)), opn, hi, lo, prc
            FROM moex.mt5_futures
            WHERE ticker = '{ticker}' AND tf = 'H1'
            ORDER BY bt DESC LIMIT 30*24
        """).result_rows
        ch.close()
        if not r:
            return None
        r = r[::-1]  # хронологически
        # Группируем по дате (МСК → берём как есть, dayofweek считает по календарным дням)
        days = {}
        for ts, o, h, l, c in r:
            d = datetime.fromtimestamp(ts).date()
            days[d] = c  # последний H1 дня = close
        daily = sorted(days.items())
        bars = [{'ts': int(datetime(d.year, d.month, d.day, 23, 0).timestamp()), 'opn': c, 'hi': c,
                 'lo': c, 'prc': c} for d, c in daily]
        # ts последнего = сейчас (не конец дня)
        bars[-1]['ts'] = int(datetime.now().timestamp())
        return {'prc': bars[-1]['prc'], 'hi': bars[-1]['hi'], 'lo': bars[-1]['lo'],
                'close_hist': [b['prc'] for b in bars],
                'hi_hist': [b['hi'] for b in bars], 'lo_hist': [b['lo'] for b in bars],
                'bars_list': bars, 'ts': bars[-1]['ts']}
    except Exception as e:
        print(f'build_daily_data {ticker} ERR: {e}', file=sys.stderr)
        return None

def insert_signal(conn, strategy, ticker, direction, entry_price, params):
    """INSERT new-сигнала; UNIQUE-конфликт = уже был → skip."""
    cur = conn.cursor()
    valid_until = datetime.now(timezone.utc) + timedelta(minutes=30)  # сигнал живёт 30 мин
    cur.execute("""
        INSERT INTO futures.signals (strategy, ticker, direction, entry_price, signal_ts, valid_until, params, status)
        VALUES (%s, %s, %s, %s, NOW(), %s, %s, 'new')
        ON CONFLICT (strategy, ticker, direction, signal_ts) DO NOTHING
        RETURNING id
    """, (strategy, ticker, direction, entry_price, valid_until, json.dumps(params, ensure_ascii=False)))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    return row[0] if row else None

def main():
    _load_strategies()
    entries = load_portfolio()
    if not entries:
        log.info('Нет включённых стратегий в futures.portfolio')
        return
    conn = pg_conn()
    inserted = 0
    for ticker, strategy, params in entries:
        fn = STRATEGY_MAP.get(strategy)
        if not fn:
            log.warning('Неизвестная стратегия %s (тикер %s) — нет движка', strategy, ticker)
            continue
        bd = build_bar_data(ticker, strategy=strategy)
        if not bd:
            continue
        try:
            signal = fn(bd, ticker, params)
        except Exception as e:
            log.warning('Ошибка %s/%s: %s', ticker, strategy, e)
            continue
        if not signal:
            continue
        sid = insert_signal(conn, strategy, ticker, signal.get('direction'),
                            signal.get('entry_price') or bd['prc'], params)
        if sid:
            log.info('СИГНАЛ %s %s %s @ %s (id=%s)', ticker, strategy,
                     signal.get('direction'), signal.get('entry_price'), sid)
            inserted += 1
    conn.close()
    if inserted:
        log.info('Добавлено сигналов: %d', inserted)

if __name__ == '__main__':
    main()
