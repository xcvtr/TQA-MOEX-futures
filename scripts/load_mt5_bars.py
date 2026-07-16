#!/usr/bin/env python3
"""MT5 bars loader — динамический continuous через _best_secid."""
import sys, os, json, subprocess, logging
from datetime import datetime, timezone, timedelta
import clickhouse_connect as cc
import psycopg2
from psycopg2.extras import execute_values

MT5_PATH = "C:/Program Files/MetaTrader 5 FINAM/terminal64.exe"

CH = dict(host='10.0.0.60', port=8123, database='moex')
PG = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
log = logging.getLogger('mt5_bars')


def get_best_contracts():
    """Читает активные контракты из _best_secid."""
    ch = cc.get_client(**CH)
    rows = ch.query("""
        SELECT ticker, best_secid
        FROM moex._best_secid
        WHERE best_secid IS NOT NULL
        ORDER BY ticker
    """).result_rows
    ch.close()
    return {r[0]: r[1] for r in rows}


def ensure_pg_table():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""
        CREATE SCHEMA IF NOT EXISTS futures;
        CREATE TABLE IF NOT EXISTS futures.bars_1m (
            ticker TEXT, bt TIMESTAMPTZ,
            opn FLOAT8, hi FLOAT8, lo FLOAT8, prc FLOAT8,
            vol INT, tick_vol INT,
            PRIMARY KEY (ticker, bt)
        )
    """)
    conn.commit()
    cur.close(); conn.close()
    log.info("PG table futures.bars_1m ready")


def pg_autopurge():
    """Удалить данные старше 2 месяцев."""
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cutoff = datetime.now(timezone.utc) - timedelta(days=60)
    cur.execute("DELETE FROM futures.bars_1m WHERE bt < %s", (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    cur.close(); conn.close()
    if deleted:
        log.info(f"PG autopurge: удалено {deleted} строк старше {cutoff.date()}")


def ensure_ch_table(ch):
    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.mt5_bars (
            ticker LowCardinality(String),
            bt DateTime,
            opn Float64, hi Float64, lo Float64, prc Float64,
            vol UInt32, tick_vol UInt32
        ) ENGINE = ReplacingMergeTree()
        PARTITION BY toYYYYMM(bt)
        ORDER BY (ticker, bt)
    """)


def get_last_ts(source, ticker):
    """Получить последний timestamp для тикера из CH."""
    if source == 'ch':
        ch = cc.get_client(**CH)
        rows = ch.query(f"SELECT max(bt) FROM moex.mt5_bars WHERE ticker='{ticker}'").result_rows
        ch.close()
        return rows[0][0] if rows and rows[0][0] else None
    else:
        conn = psycopg2.connect(**PG)
        cur = conn.cursor()
        cur.execute("SELECT max(bt) FROM futures.bars_1m WHERE ticker=%s", (ticker,))
        r = cur.fetchone()
        cur.close(); conn.close()
        return r[0] if r and r[0] else None


def fetch_bars_via_wine(symbols_dict, from_dates):
    """Вызвать wine python для загрузки свежих баров из MT5."""
    script = '''
import MetaTrader5 as mt5, json, sys
from datetime import datetime, timezone

mt5.initialize(path=r'%(path)s')
symbols = %(symbols)s
result = {}
for ticker, mt5_name in symbols:
    info = mt5.symbol_info(mt5_name)
    if info is None:
        result[ticker] = {"error": "symbol not found"}
        continue
    rates = mt5.copy_rates_from_pos(mt5_name, mt5.TIMEFRAME_M1, 0, 30)
    if rates is None or len(rates) == 0:
        result[ticker] = {"error": "no data"}
        continue
    bars = []
    for r in rates:
        ts = datetime.fromtimestamp(r[0], tz=timezone.utc)
        bars.append({"ts":str(ts),"opn":float(r[1]),"hi":float(r[2]),"lo":float(r[3]),"prc":float(r[4]),"vol":int(r[5]),"tick_vol":int(r[6])})
    result[ticker] = {"bars": bars, "count": len(bars)}
mt5.shutdown()
print(json.dumps(result))
''' % {"symbols": json.dumps(symbols_dict), "path": MT5_PATH}
    
    proc = subprocess.run(['wine', 'python', '-c', script],
        capture_output=True, text=True, timeout=30)
    
    if proc.returncode != 0:
        log.error(f"wine python error: {proc.stderr[:300]}")
        return None
    
    for line in proc.stdout.split('\n'):
        line = line.strip()
        if line.startswith('{'):
            return json.loads(line)
    
    log.error(f"parse failed: {proc.stdout[:200]}")
    return None


def insert_to_pg(ticker, rows):
    """Вставить бары в PG с ON CONFLICT DO NOTHING."""
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    execute_values(cur,
        "INSERT INTO futures.bars_1m (ticker, bt, opn, hi, lo, prc, vol, tick_vol) VALUES %s ON CONFLICT DO NOTHING",
        [(ticker, r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in rows],
        template="(%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    conn.commit()
    cur.close(); conn.close()


def insert_to_ch(ch, ticker, rows):
    """Вставить бары в CH."""
    batch_size = 10000
    for i in range(0, len(rows), batch_size):
        ch.insert('moex.mt5_bars', rows[i:i+batch_size],
            column_names=['ticker', 'bt', 'opn', 'hi', 'lo', 'prc', 'vol', 'tick_vol'])


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
    
    # PG таблица
    ensure_pg_table()
    
    # CH таблица
    ch = cc.get_client(**CH)
    ensure_ch_table(ch)
    
    # Загружаем бары из MT5 — динамический continuous из _best_secid
    mt5_symbols = get_best_contracts()
    if not mt5_symbols:
        log.error("Нет контрактов в _best_secid!")
        sys.exit(1)
    log.info(f"Контракты: {mt5_symbols}")
    
    data = fetch_bars_via_wine(list(mt5_symbols.items()), {})
    if data is None:
        sys.exit(1)
    
    now = datetime.now()
    for ticker, td in data.items():
        if 'error' in td:
            log.warning(f"{ticker}: {td['error']}")
            continue
        
        bars = td['bars']
        if not bars:
            continue
        
        rows = []
        for b in bars:
            ts = b['ts']
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace('+00:00', ''))
            if ts > now:
                continue
            rows.append((ticker, ts, b['opn'], b['hi'], b['lo'], b['prc'], b['vol'], b['tick_vol']))
        
        if not rows:
            continue
        
        # PG (live, autopurge будет ниже)
        insert_to_pg(ticker, rows)
        
        # CH (история)
        insert_to_ch(ch, ticker, rows)
        
        log.info(f"{ticker}: {len(rows)} bars ({rows[0][1]} -> {rows[-1][1]})")
    
    # Autopurge PG — удалить старше 60 дней
    pg_autopurge()
    
    ch.close()
    log.info("Done")
