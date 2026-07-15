#!/usr/bin/env python3
"""MT5 bars loader for MOEX futures — инкрементальная загрузка в CH."""
import sys, os, json, subprocess, logging
from datetime import datetime, timezone, timedelta
import clickhouse_connect as cc

CH = dict(host='10.0.0.60', port=8123, database='moex')
log = logging.getLogger('mt5_bars')

MT5_SYMBOLS = {
    'Si': 'SiU6', 'GZ': 'GZU6', 'BR': 'BRU6', 'CR': 'CRU6',
    'GD': 'GDU6', 'MM': 'MMU6', 'NG': 'NGU6', 'RN': 'RNU6',
}


def get_ch():
    return cc.get_client(**CH)


def ensure_tables(ch):
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


def get_last_ts(ch, ticker):
    rows = ch.query(f"SELECT max(bt) FROM moex.mt5_bars WHERE ticker='{ticker}'").result_rows
    return rows[0][0] if rows and rows[0][0] else None


def fetch_bars_via_wine(symbols_dict, from_dates):
    """Вызвать wine python для загрузки баров."""
    script = '''
import MetaTrader5 as mt5, json, sys
from datetime import datetime, timezone

mt5.initialize()
symbols = %(symbols)s
from_dates = %(dates)s

result = {}
for ticker, mt5_name in symbols:
    info = mt5.symbol_info(mt5_name)
    if info is None:
        result[ticker] = {"error": "symbol not found"}
        continue
    
    from_date = from_dates.get(ticker)
    if from_date:
        from_dt = datetime.fromisoformat(from_date)
        rates = mt5.copy_rates_from(mt5_name, mt5.TIMEFRAME_M1, from_dt, 100000)
    else:
        rates = mt5.copy_rates_from_pos(mt5_name, mt5.TIMEFRAME_M1, 0, 100000)
    
    if rates is None or len(rates) == 0:
        result[ticker] = {"error": "no data"}
        continue
    
    bars = []
    for r in rates:
        ts = datetime.fromtimestamp(r[0], tz=timezone.utc)
        bars.append({
            "ts": str(ts), "opn": float(r[1]), "hi": float(r[2]),
            "lo": float(r[3]), "prc": float(r[4]),
            "vol": int(r[5]), "tick_vol": int(r[6])
        })
    result[ticker] = {"bars": bars, "count": len(bars)}

mt5.shutdown()
print(json.dumps(result))
''' % {"symbols": json.dumps(symbols_dict), "dates": json.dumps(from_dates)}
    
    proc = subprocess.run(['wine', 'python', '-c', script],
        capture_output=True, text=True, timeout=180)
    
    if proc.returncode != 0:
        log.error(f"wine python error: {proc.stderr[:500]}")
        return None
    
    for line in proc.stdout.split('\n'):
        line = line.strip()
        if line.startswith('{'):
            return json.loads(line)
    
    log.error(f"parse failed: {proc.stdout[:300]}")
    return None


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
    
    ch = get_ch()
    ensure_tables(ch)
    
    # Определяем с какой даты загружать (инкрементально)
    from_dates = {}
    for ticker in MT5_SYMBOLS:
        last_ts = get_last_ts(ch, ticker)
        if last_ts:
            from_dates[ticker] = (last_ts - timedelta(hours=1)).isoformat()
            log.info(f"{ticker}: last={last_ts}, loading from {from_dates[ticker]}")
        else:
            log.info(f"{ticker}: no data, full load")
    
    # Загружаем
    data = fetch_bars_via_wine(list(MT5_SYMBOLS.items()), from_dates)
    if data is None:
        sys.exit(1)
    
    # Вставляем в CH
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
            rows.append((
                ticker, ts, b['opn'], b['hi'], b['lo'], b['prc'],
                b['vol'], b['tick_vol']
            ))
        
        if rows:
            batch_size = 10000
            for i in range(0, len(rows), batch_size):
                ch.insert('moex.mt5_bars', rows[i:i+batch_size],
                    column_names=['ticker', 'bt', 'opn', 'hi', 'lo', 'prc', 'vol', 'tick_vol'])
            log.info(f"{ticker}: {len(rows)} bars ({rows[0][1]} -> {rows[-1][1]})")
    
    log.info("Done")
