#!/usr/bin/env python3
"""AlgoPack multi-threaded loader — one thread per date range."""

import os, sys, time, pandas as pd, re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from algopack_token import TOKEN as ALGOPACK_TOKEN

TABLE = os.environ.get('TABLE', 'tradestats')
CH_HOST = os.environ.get('CLICKHOUSE_HOST', '10.0.0.64')
CH_DB = os.environ.get('CH_DB', 'moex_algopack_v2')
MARKET_MAP = {'tradestats': ('EQ', 'tradestats'), 'obstats': ('EQ', 'obstats')}
NUM_WORKERS = int(os.environ.get('ALGOPACK_WORKERS', '4'))

from moexalgo import session, Market
session.TOKEN = ALGOPACK_TOKEN


def df_sql(df, table):
    if df.empty:
        return None
    cols = list(df.columns)
    rows = []
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if pd.isna(v):
                vals.append('NULL')
            elif isinstance(v, (int, float)):
                vals.append(str(v))
            elif isinstance(v, pd.Timestamp):
                vals.append("'" + v.strftime('%Y-%m-%d %H:%M:%S.%f') + "'")
            elif isinstance(v, datetime):
                vals.append("'" + v.strftime('%Y-%m-%d %H:%M:%S') + "'")
            else:
                s = str(v).replace("'", "''")
                vals.append("'" + s + "'")
        rows.append('(' + ', '.join(vals) + ')')
    return "INSERT INTO {}.{} ({}) VALUES\n{}".format(
        CH_DB, table, ', '.join(cols), ',\n'.join(rows))


def ins(sql):
    import urllib.request
    try:
        r = urllib.request.urlopen(
            'http://' + CH_HOST + ':8123/',
            data=sql.encode('utf-8'),
            timeout=120)
        r.read()
        return True
    except Exception as e:
        print('CH fail:', e, file=sys.stderr)
        return False


def process_day(market, method, ds):
    """Fetch one day, insert to CH. Returns (ds, n_rows) or (ds, error)."""
    try:
        df = method(date=ds)
    except Exception as ex:
        return (ds, 'err: ' + str(ex))
    if df is None or df.empty:
        return (ds, 0)
    n = len(df)
    sql = df_sql(df, TABLE)
    if sql:
        ok = ins(sql)
        if not ok:
            return (ds, 'CH fail')
    return (ds, n)


def worker_thread(thread_id, dates, results):
    """Each thread creates its own Market instance."""
    from moexalgo import session as sess, Market as Mkt
    sess.TOKEN = ALGOPACK_TOKEN
    market_name, method_name = MARKET_MAP[TABLE]
    market = Mkt(market_name)
    method = getattr(market, method_name)
    total = 0

    for ds in dates:
        res = process_day(market, method, ds)
        n = res[1]
        if isinstance(n, int) and n > 0:
            total += n
        results[thread_id] = (ds, total)
        sys.stdout.write('{}: {} -> {}\n'.format(thread_id, ds, '{} rows'.format(n) if isinstance(n, int) else n))
        sys.stdout.flush()
        time.sleep(0.2)

    results[thread_id] = ('DONE', total)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sf = os.path.join(script_dir, 'state_' + TABLE + '.txt')
    last = '2020-01-01'
    if os.path.exists(sf):
        with open(sf) as f:
            last = f.read().strip()
        last = re.sub(r'^\d+,', '', last)

    # Get last date from CH too — pick the max
    import clickhouse_connect
    ch = clickhouse_connect.get_client(host=CH_HOST)
    row = ch.query('SELECT max(tradedate) FROM {}.{}'.format(CH_DB, TABLE)).result_rows[0]
    ch_last = row[0]
    if ch_last and ch_last.year > 2000:
        ch_last_s = ch_last.strftime('%Y-%m-%d')
        if ch_last_s > last:
            last = ch_last_s
            print('Resuming from CH max date:', last, flush=True)
        else:
            print('Resuming from state file:', last, flush=True)

    start = datetime.strptime(last, '%Y-%m-%d')
    end = datetime.now() - timedelta(days=1)

    # Build list of all trading days
    all_dates = []
    d = start
    while d <= end:
        all_dates.append(d.strftime('%Y-%m-%d'))
        d += timedelta(1)

    print('Total days to process: {} ({} to {})'.format(
        len(all_dates), all_dates[0], all_dates[-1]), flush=True)

    # Split into chunks for each worker
    chunk_size = (len(all_dates) + NUM_WORKERS - 1) // NUM_WORKERS
    chunks = [all_dates[i:i + chunk_size] for i in range(0, len(all_dates), chunk_size)]
    print('{} workers, {} chunks'.format(len(chunks), len(chunks)), flush=True)

    import threading
    results = {}
    threads = []
    for tid, chunk in enumerate(chunks):
        t = threading.Thread(target=worker_thread, args=(tid, chunk, results))
        threads.append(t)
        t.start()

    # Monitor progress
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(15)
            statuses = []
            for tid in range(len(chunks)):
                if tid in results:
                    r = results[tid]
                    statuses.append('{}:{}'.format(tid, r[0] if isinstance(r, tuple) else r))
            line = '[{}] '.format(datetime.now().strftime('%H:%M:%S')) + ' | '.join(statuses) + '\n'
            sys.stdout.write(line)
            sys.stdout.flush()
            # Also print via stderr so background capture sees it
            sys.stderr.write(line)
            sys.stderr.flush()
    except KeyboardInterrupt:
        print('\nInterrupted, waiting for threads...', flush=True)

    for t in threads:
        t.join()

    # Summary
    grand_total = 0
    for tid in range(len(chunks)):
        if tid in results:
            r = results[tid]
            if isinstance(r, tuple) and len(r) == 2 and r[0] == 'DONE':
                grand_total += r[1]

    # Save last processed date
    max_date = ch.query('SELECT max(tradedate) FROM {}.{}'.format(CH_DB, TABLE)).result_rows[0][0]
    if max_date and max_date.year > 2000:
        with open(sf, 'w') as f:
            f.write(max_date.strftime('%Y-%m-%d'))

    print('\nALL DONE. Total rows: {}'.format(grand_total), flush=True)


if __name__ == '__main__':
    main()
