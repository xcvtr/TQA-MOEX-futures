#!/usr/bin/env python3 -u
"""Заполнить futures.bars_d1 (дневные close) из CH mt5_continuous.

Зачем: dayofweek (SBRF/SPYF) требует 60 дней дневных close для prev_week_return.
Live не должен зависеть от CH — дневные бары живут в PG.
Источник: CH mt5_continuous (полная история), агрегация: последний close дня (будни).
Cron: каждый вечер после торговой сессии (например 0 14 * * 1-5 UTC = 21:00 IRK).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import clickhouse_connect as cc
import psycopg2
from datetime import datetime, timezone, timedelta

CH_HOST = os.getenv('MOEX_CH_HOST', '10.0.0.60')
PG_HOST = os.getenv('MOEX_PG_HOST', '10.0.0.60')
PG_PORT = int(os.getenv('MOEX_PG_PORT', '5432'))
PG_DB = os.getenv('MOEX_PG_DB', 'moex')
PG_USER = os.getenv('MOEX_PG_USER', 'postgres')
PG_PASS = os.getenv('MOEX_PG_PASSWORD', '')


def pg_conn():
    return psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user=PG_USER, password=PG_PASS, connect_timeout=5)

TICKERS = ['SBRF', 'SPYF']  # dayofweek тикеры
DAYS = 90  # глубина дневных close (нужно 60 + запас)


def main():
    ch = cc.get_client(host=CH_HOST, port=8123, database='moex')
    conn = pg_conn()
    cur = conn.cursor()
    total = 0
    for tk in TICKERS:
        r = ch.query(f"""
            SELECT toUnixTimestamp(toDateTime(bt)), prc
            FROM moex.mt5_continuous
            WHERE ticker = '{tk}' AND bt >= now() - INTERVAL {DAYS} DAY
            ORDER BY bt
        """).result_rows
        if not r:
            print(f'{tk}: нет данных в CH', flush=True)
            continue
        # Агрегация по дате (IRK): последний close буднего дня
        days = {}
        for ts, prc in r:
            d = datetime.fromtimestamp(ts).date()
            if d.weekday() >= 5:
                continue
            days[d] = prc
        n = 0
        for d, c in sorted(days.items()):
            cur.execute(
                "INSERT INTO futures.bars_d1 (ticker, d, prc) VALUES (%s,%s,%s) "
                "ON CONFLICT (ticker, d) DO UPDATE SET prc = EXCLUDED.prc",
                (tk, d, float(c)))
            n += 1
        conn.commit()
        print(f'{tk}: {n} дневных close записано', flush=True)
        total += n
    # autopurge: старше 120 дней — удаляем (глубина для prev_week_return не нужна)
    cur.execute("DELETE FROM futures.bars_d1 WHERE d < now()::date - 120")
    conn.commit()
    cur.close(); conn.close(); ch.close()
    print(f'Всего: {total} дневных close (autopurge 120 дней)', flush=True)


if __name__ == '__main__':
    main()
