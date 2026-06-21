#!/usr/bin/env python3
"""
Bulk loader: MOEX AlgoPack fo/ -> ClickHouse moex.*_fo tables.
Fetches ALL tickers for a date at once (no per-ticker filter).
Handles type conversion (API strings -> CH types).
"""
import sys, os, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta, datetime
import requests
import clickhouse_connect

CH_HOST = "10.0.0.63"
CH_PORT = 8123
CH_DB = "moex"
API_BASE = "https://apim.moex.com/iss/datashop/algopack/fo"

# read token
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
__tok = None
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if 'ALGOPACK_APIKEY' in line:
                parts = line.strip().split('=', 1)
                if len(parts) == 2:
                    __tok = parts[1].strip()
                break
if not __tok:
    sys.exit("FATAL: ALGOPACK_APIKEY not found in .env")

HEADERS = {"Authorization": "Bearer " + __tok}

# Column definitions aligned with CH tables
TS_COLS_RAW = ["tradedate","tradetime","secid","asset_code",
    "pr_open","pr_high","pr_low","pr_close","pr_std","vol","val",
    "trades","pr_vwap","pr_change","trades_b","trades_s",
    "val_b","val_s","vol_b","vol_s","disb","pr_vwap_b","pr_vwap_s",
    "im","oi_open","oi_high","oi_low","oi_close",
    "sec_pr_open","sec_pr_high","sec_pr_low","sec_pr_close","SYSTIME"]

# Type converters per column
def _conv_tradedate(v):
    if v is None or v == '': return None
    return date.fromisoformat(v) if isinstance(v, str) else v

def _conv_systime(v):
    if v is None or v == '': return None
    if isinstance(v, str):
        v = v.replace('T', ' ')
        if '.' in v:
            return datetime.strptime(v, '%Y-%m-%d %H:%M:%S.%f')
        return datetime.strptime(v, '%Y-%m-%d %H:%M:%S')
    return v

def _conv_null(v):
    if v == '' or v is None: return None
    return v

TS_CONV = {
    'tradedate': _conv_tradedate,
    'SYSTIME': _conv_systime,
}

OBS_COLS_RAW = ["tradedate","tradetime","secid","asset_code",
    "mid_price","micro_price","spread_l1",
    "levels_b","levels_s",
    "vol_b_l1","vol_s_l1","vol_b_l2","vol_s_l2",
    "vol_b_l3","vol_s_l3","vol_b_l5","vol_s_l5",
    "vol_b_l10","vol_s_l10","vol_b_l20","vol_s_l20",
    "vwap_b_l3","vwap_s_l3","SYSTIME"]

ORD_COLS_RAW = ["tradedate","tradetime","secid","asset_code",
    "put_cancel_ratio","orders_b_put","orders_s_put",
    "orders_b_cancel","orders_s_cancel",
    "vwap_b","vwap_s","SYSTIME"]

DATASETS = {
    "tradestats": {"cols_raw": TS_COLS_RAW, "table": "tradestats_fo", "conv": TS_CONV},
    "obstats":    {"cols_raw": OBS_COLS_RAW, "table": "obstats_fo",
                   "conv": {'tradedate': _conv_tradedate, 'SYSTIME': _conv_systime}},
    "orderstats": {"cols_raw": ORD_COLS_RAW, "table": "orderstats_fo",
                   "conv": {'tradedate': _conv_tradedate, 'SYSTIME': _conv_systime}},
}

def convert_row(cols_raw, row, conv_map):
    """Convert raw API row (list of values) to list with proper types (for column_names insert)."""
    converted = []
    for c, v in zip(cols_raw, row):
        if c in conv_map:
            converted.append(conv_map[c](v))
        else:
            converted.append(_conv_null(v))
    return converted

def fetch_date_all(dataset, date_str, cols_raw, conv_map):
    """Fetch ALL rows for one date, paginated. Returns list of dicts."""
    url = f"{API_BASE}/{dataset}.json"
    all_rows = []
    start = 0
    while True:
        try:
            params = {"date": date_str, "limit": 1000, "start": start}
            r = requests.get(url, params=params, headers=HEADERS, timeout=60)
            if r.status_code != 200:
                break
            rows = r.json().get("data", {}).get("data", [])
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < 1000:
                break
            start += 1000
        except Exception as e:
            print(f"  ERR {dataset} {date_str}: {e}", file=sys.stderr)
            time.sleep(5)
            continue
    if not all_rows:
        return []
    return [convert_row(cols_raw, row, conv_map) for row in all_rows]

def insert_batch(ch, table, rows, cols_raw):
    if not rows:
        return
    try:
        ch.insert(table, rows, column_names=cols_raw)
    except Exception as e:
        print(f"  CH INSERT ERR {table}: {e}", file=sys.stderr)

def generate_dates(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2020-01-03")
    parser.add_argument("--end", default="2026-06-21")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--datasets", nargs="+", default=["tradestats"])
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    workers = args.workers

    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    ch.command("SET max_partitions_per_insert_block = 0")
    ch.command("SET async_insert = 1")
    ch.command("SET wait_for_async_insert = 0")

    dates = list(generate_dates(start_date, end_date))
    print(f"Dates: {len(dates)} ({dates[0]} .. {dates[-1]})", file=sys.stderr)

    for ds_name in args.datasets:
        if ds_name not in DATASETS:
            continue
        ds = DATASETS[ds_name]
        table = ds["table"]
        cols_raw = ds["cols_raw"]
        conv_map = ds["conv"]

        # Check which dates already loaded
        existing = set()
        try:
            res = ch.query(f"SELECT DISTINCT tradedate FROM {table}").result_rows
            existing = {str(r[0]) for r in res}
        except Exception:
            pass

        pending = [d for d in dates if d not in existing]
        print(f"\n{ds_name} -> {table}: {len(dates)} total, {len(existing)} existing, {len(pending)} pending", file=sys.stderr)

        if not pending:
            continue

        t0 = time.time()
        total_rows = 0
        done = 0
        batch_buffer = []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fetch_date_all, ds_name, d, cols_raw, conv_map): d for d in pending}

            for fut in as_completed(futs):
                d = futs[fut]
                rows = fut.result()
                done += 1

                if rows:
                    batch_buffer.extend(rows)
                    total_rows += len(rows)
                    if len(batch_buffer) >= 50000:
                        insert_batch(ch, table, batch_buffer, cols_raw)
                        batch_buffer = []

                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(pending) - done) / rate if rate > 0 else 0
                if done % 20 == 0 or done == len(pending):
                    print(f"  {ds_name}: {done}/{len(pending)} days, {total_rows} rows, "
                          f"{rate:.1f}/s, ETA {eta:.0f}s", file=sys.stderr)

            if batch_buffer:
                insert_batch(ch, table, batch_buffer, cols_raw)

        elapsed = time.time() - t0
        print(f"  DONE {ds_name}: {total_rows} rows in {elapsed:.0f}s "
              f"({total_rows/elapsed:.0f} rows/s)", file=sys.stderr)

    ch.close()
    print("\nALL DONE!", file=sys.stderr)

if __name__ == "__main__":
    main()
