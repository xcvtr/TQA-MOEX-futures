#!/usr/bin/env python3 -u
"""Pull continuous M1 bars from FINAM MT5 directly into ClickHouse."""
import sys
from datetime import datetime

CONT_SYMBOLS = {
    'BR': 'ALLFUTBR', 'Si': 'ALLFUTSi', 'CR': 'ALLFUTCNY',
    'GZ': 'ALLFUTGAZR', 'GD': 'ALLFUTGOLD', 'MM': 'MOEXMM',
    'NG': 'ALLFUTNG', 'RN': 'ALLFUTROSN', 'SV': 'ALLFUTSILV',
}

MT5_PATH = "C:/Program Files/MetaTrader 5 FINAM/terminal64.exe"
CH = dict(host='10.0.0.60', port=8123, database='moex')


def pull_and_save(ticker, mt5_name, start_year=2020, end_year=2026):
    import MetaTrader5 as mt5
    import clickhouse_connect as cc

    mt5.initialize(path=MT5_PATH)
    ch = cc.get_client(**CH)

    ch.command("""
        CREATE TABLE IF NOT EXISTS moex.mt5_continuous (
            ticker LowCardinality(String),
            bt DateTime,
            opn Float64, hi Float64, lo Float64, prc Float64,
            vol UInt32, tick_vol UInt32
        ) ENGINE = ReplacingMergeTree()
        PARTITION BY toYYYYMM(bt)
        ORDER BY (ticker, bt)
    """)

    batch_size = 10000
    total = 0

    for year in range(start_year, end_year + 1):
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1) if year < end_year else datetime.now()
        rates = mt5.copy_rates_range(mt5_name, mt5.TIMEFRAME_M1, start, end)
        if rates is None or len(rates) == 0:
            print(f'  {ticker:4s} {year}: no data', flush=True)
            continue

        rows = []
        for r in rates:
            ts = datetime.fromtimestamp(r[0])
            rows.append((ticker, ts, float(r[1]), float(r[2]), float(r[3]),
                        float(r[4]), int(r[5]), int(r[6])))

        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            ch.insert('moex.mt5_continuous', chunk,
                column_names=['ticker', 'bt', 'opn', 'hi', 'lo', 'prc', 'vol', 'tick_vol'])
            total += len(chunk)

        first = datetime.fromtimestamp(rates[0][0])
        last = datetime.fromtimestamp(rates[-1][0])
        print(f'  {ticker:4s} {year}: {len(rows):>6d} bars  {first} -> {last}  (total: {total})', flush=True)

    mt5.shutdown()
    ch.close()
    return total


if __name__ == '__main__':
    print('Pulling continuous M1 -> CH moex.mt5_continuous', flush=True)
    grand_total = 0
    for ticker, mt5_name in sorted(CONT_SYMBOLS.items()):
        print(f'\n{ticker} ({mt5_name}):', flush=True)
        n = pull_and_save(ticker, mt5_name)
        grand_total += n
    print(f'\nDone! Total: {grand_total} bars', flush=True)
