#!/usr/bin/env python3
"""Обновление дат экспирации (LASTTRADEDATE) для OI-тикеров из ISS.

Крон: 30 6 * * 1-5 (вместе с update_go_ksur_pgo.py)
Пишет в PG futures.ticker_specs.expiration_date (date или NULL).

Ролл continuous ALLFUT происходит в последний день торговли контрактом
(LASTTRADEDATE) — в этот день не открываем новые позиции и закрываем
открытые до вечерней склейки (см. is_roll_day в paper_trader).
"""
import json
import sys
import urllib.request
from datetime import date, datetime

import psycopg2

PG_HOST = '10.0.0.60'
PG_PORT = 5432
PG_DB = 'moex'
PG_USER = 'postgres'

# ASSETCODE в ISS → наш ticker
TICKER_MAP = {
    'BR': 'BR',
    'NG': 'NG',
    'SILV': 'SV',
    'ROSN': 'RN',
}

ISS_URL = ('https://iss.moex.com/iss/engines/futures/markets/forts/securities.json'
           '?iss.meta=off&iss.only=securities&limit=500'
           '&securities.columns=SECID,LASTTRADEDATE,ASSETCODE')


def fetch_last_tradedate() -> dict:
    """Возвращает {ticker: date} — ближайшая будущая LASTTRADEDATE активного контракта."""
    with urllib.request.urlopen(ISS_URL, timeout=30) as f:
        data = json.load(f)
    cols = data['securities']['columns']
    rows = data['securities']['data']
    result = {}
    for r in rows:
        d = dict(zip(cols, r))
        asset = d.get('ASSETCODE')
        ltd = d.get('LASTTRADEDATE')
        if asset not in TICKER_MAP or not ltd:
            continue
        ticker = TICKER_MAP[asset]
        exp = datetime.strptime(ltd, '%Y-%m-%d').date()
        # ближайшая дата >= сегодня (активный контракт)
        if exp >= date.today():
            if ticker not in result or exp < result[ticker]:
                result[ticker] = exp
    return result


def main():
    exps = fetch_last_tradedate()
    if not exps:
        print('Нет данных ISS (сеть?) — пропускаю', flush=True)
        sys.exit(0)

    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user=PG_USER, connect_timeout=5)
    cur = conn.cursor()
    # колонка expiration_date, если нет
    cur.execute("""
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema='futures' AND table_name='ticker_specs'
          AND column_name='expiration_date'
    """)
    if cur.fetchone()[0] == 0:
        cur.execute("ALTER TABLE futures.ticker_specs ADD COLUMN expiration_date DATE")
        print('Добавлена колонка expiration_date', flush=True)

    for ticker, exp in exps.items():
        cur.execute("""
            UPDATE futures.ticker_specs SET expiration_date = %s WHERE ticker = %s
        """, (exp, ticker))
        print(f'{ticker}: LASTTRADEDATE = {exp}', flush=True)
    conn.commit()
    conn.close()


if __name__ == '__main__':
    main()
