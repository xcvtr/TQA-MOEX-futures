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
    """Дневные close из PG bars_d1 (live-источник), fallback CH mt5_continuous.
    История SPYF/SBRF полная (2024+) — берём 60 дней, хватает для prev_week_return."""
    # 1. PG bars_d1 (основной — заполняется load_bars_d1.py из CH)
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT d, prc FROM futures.bars_d1
            WHERE ticker = %s AND d >= now()::date - 60
            ORDER BY d
        """, (ticker,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        if len(rows) >= 3:
            from datetime import datetime as _dt
            bars = [{'ts': int(_dt(d.year, d.month, d.day, 23, 0).timestamp()),
                     'opn': float(c), 'hi': float(c), 'lo': float(c), 'prc': float(c)}
                    for d, c in rows]
            bars[-1]['ts'] = int(datetime.now(timezone.utc).timestamp())
            return {'prc': bars[-1]['prc'], 'hi': bars[-1]['hi'], 'lo': bars[-1]['lo'],
                    'close_hist': [b['prc'] for b in bars],
                    'hi_hist': [b['hi'] for b in bars], 'lo_hist': [b['lo'] for b in bars],
                    'bars_list': bars, 'ts': bars[-1]['ts']}
    except Exception:
        pass
    # 2. CH mt5_continuous (fallback)
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
    bars[-1]['ts'] = int(datetime.now(timezone.utc).timestamp())
    return {'prc': bars[-1]['prc'], 'hi': bars[-1]['hi'], 'lo': bars[-1]['lo'],
            'close_hist': [b['prc'] for b in bars],
            'hi_hist': [b['hi'] for b in bars], 'lo_hist': [b['lo'] for b in bars],
            'bars_list': bars, 'ts': bars[-1]['ts']}


def _build_m1(ticker):
    """M1 из PG bars_1m (live-источник). CH не используется для live."""
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT EXTRACT(EPOCH FROM bt)::bigint, opn, hi, lo, prc, vol
            FROM futures.bars_1m
            WHERE ticker = %s AND bt >= now() - INTERVAL '2 days'
            ORDER BY bt
        """, (ticker,))
        r = cur.fetchall()
        cur.close(); conn.close()
        if r:
            return _bars_from_rows(r)
    except Exception:
        pass
    return None


def _bars_from_rows(r):
    """Собрать bar_data из строк (ts, opn, hi, lo, prc, vol)."""
    n = min(200, len(r))
    bars = [{'ts': int(r[-n+i][0]), 'opn': float(r[-n+i][1]), 'hi': float(r[-n+i][2]),
             'lo': float(r[-n+i][3]), 'prc': float(r[-n+i][4]), 'vol': float(r[-n+i][5] or 0)}
            for i in range(n)]
    closes = [b['prc'] for b in bars]
    bd = {
        'prc': closes[-1], 'hi': bars[-1]['hi'], 'lo': bars[-1]['lo'],
        'close_hist': closes, 'hi_hist': [b['hi'] for b in bars],
        'lo_hist': [b['lo'] for b in bars], 'vol_hist': [b['vol'] for b in bars],
        'bars_list': bars,
        'ts': bars[-1]['ts'],
    }
    return bd


def attach_day_net(bd, ticker):
    """Присвоить day_net (из PG futoi_iss) каждому бару — для OI-стратегии."""
    if not bd or not bd.get('bars_list'):
        return bd
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT EXTRACT(EPOCH FROM bt)::bigint, buy_fiz, sell_fiz, buy_yur, sell_yur
            FROM futures.futoi_iss
            WHERE ticker = %s AND bt >= now() - INTERVAL '72 hours'
            ORDER BY bt
        """, (ticker,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        if not rows:
            return bd
        # day_start = первая запись IRK-дня (07:00 UTC)
        day_start = {}
        dn_by_ts = {}
        for ts, fb, fs, yb, ys in rows:
            d = int((ts - 7 * 3600) // 86400)
            if d not in day_start:
                day_start[d] = int(fb) - int(fs)
            total = int(fb) + int(fs) + int(yb) + int(ys)
            if total > 0:
                dn_by_ts[ts] = (int(fb) - int(fs) - day_start[d]) / total * 100.0
        import bisect as _bisect
        dn_ts = sorted(dn_by_ts.keys())
        for b in bd['bars_list']:
            i = _bisect.bisect_right(dn_ts, b['ts']) - 1
            if i >= 0:
                b['day_net'] = dn_by_ts[dn_ts[i]]
        # day_net для последнего бара (детект смотрит bars[-1])
        last = bd['bars_list'][-1]
        if 'day_net' in last:
            bd['day_net'] = last['day_net']
    except Exception:
        pass
    return bd


def build_daily_data(ticker):
    """Дневные бары dayofweek из PG bars_d1 (live-источник), fallback CH 10.0.0.63.
    H1 → агрегируем в дневные close (последний H1 дня). ts = конец дня."""
    # 1. PG bars_d1 (основной)
    try:
        conn = pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT d, prc FROM futures.bars_d1
            WHERE ticker = %s AND d >= now()::date - 60
            ORDER BY d
        """, (ticker,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        if len(rows) >= 3:
            bars = [{'ts': int(datetime(d.year, d.month, d.day, 23, 0).timestamp()),
                     'opn': float(c), 'hi': float(c), 'lo': float(c), 'prc': float(c)}
                    for d, c in rows]
            bars[-1]['ts'] = int(datetime.now(timezone.utc).timestamp())
            return {'prc': bars[-1]['prc'], 'hi': bars[-1]['hi'], 'lo': bars[-1]['lo'],
                    'close_hist': [b['prc'] for b in bars],
                    'hi_hist': [b['hi'] for b in bars], 'lo_hist': [b['lo'] for b in bars],
                    'bars_list': bars, 'ts': bars[-1]['ts']}
    except Exception:
        pass
    # 2. CH 10.0.0.63 (fallback)
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
        bars[-1]['ts'] = int(datetime.now(timezone.utc).timestamp())
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
        # Для OI: подгрузить day_net из PG futoi_iss
        if strategy in ('oi', 'oi_dom'):
            bd = attach_day_net(bd, ticker)
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
