#!/bin/bash
set -e
cd /home/user/projects/TQA-MOEX

# Читаем токен
LINE=$(grep ALGOPACK_APIKEY .env)
TOKEN="${LIN...ing token len: ${#TOKEN}"

cat > /tmp/algopack_loader_final.py << PYTHON_SCRIPT
#!/usr/bin/env python3
import sys, json, requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import clickhouse_driver

TOKEN = "${TOKEN}"
HEADERS = {"Authorization": "Bearer " + TOKEN}
CH = clickhouse_driver.Client(host="10.0.0.63", port=9000, database="moex_algopack")

TS_COLS = ["tradedate","tradetime","secid","asset_code",
    "pr_open","pr_high","pr_low","pr_close","pr_std","vol","val",
    "trades","pr_vwap","pr_change","trades_b","trades_s",
    "val_b","val_s","vol_b","vol_s","disb","pr_vwap_b","pr_vwap_s",
    "im","oi_open","oi_high","oi_low","oi_close","systime"]
OBS_COLS = ["tradedate","tradetime","secid","asset_code",
    "mid_price","micro_price","spread_l1",
    "levels_b","levels_s",
    "vol_b_l1","vol_s_l1","vol_b_l2","vol_s_l2",
    "vol_b_l3","vol_s_l3","vol_b_l5","vol_s_l5",
    "vol_b_l10","vol_s_l10","vol_b_l20","vol_s_l20",
    "vwap_b_l3","vwap_s_l3","systime"]

def get_tickers(dataset):
    url = f"https://apim.moex.com/iss/datashop/algopack/fo/{dataset}.json"
    r = requests.get(url, params={"date": "2025-06-17", "limit": 5000}, headers=HEADERS)
    rows = r.json().get("data", {}).get("data", [])
    return sorted(set(rr[2] for rr in rows if len(rr) > 3))

def fetch_one(dataset, ticker, date_str, cols):
    url = f"https://apim.moex.com/iss/datashop/algopack/fo/{dataset}.json"
    all_rows = []
    start = 0
    while True:
        try:
            r = requests.get(url, params={"date": date_str, "secid": ticker, "limit": 1000, "start": start}, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                break
            j = r.json()
            rows = j.get("data", {}).get("data", [])
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < 1000:
                break
            start += 1000
        except Exception as e:
            print(f"ERR {dataset} {ticker} {date_str}: {e}", file=sys.stderr)
            time.sleep(2)
            continue
    if not all_rows:
        return 0
    data = [{cols[i]: v for i, v in enumerate(row)} for row in all_rows]
    try:
        CH.execute(f"INSERT INTO moex_algopack.{dataset} VALUES", data)
        return len(data)
    except Exception as e:
        print(f"CH ERR {dataset} {ticker} {date_str}: {e}", file=sys.stderr)
        return 0

def generate_dates(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)

if __name__ == "__main__":
    workers = 8
    tickers = get_tickers("tradestats")
    print(f"Tickers: {len(tickers)}", file=sys.stderr)

    dates = list(generate_dates(date(2020, 1, 1), date(2025, 6, 17)))
    print(f"Dates: {len(dates)}", file=sys.stderr)

    # TradeStats
    tasks = [(t, d) for t in tickers for d in dates]
    print(f"Total TradeStats tasks: {len(tasks)}", file=sys.stderr)
    done = 0
    total_rows = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(fetch_one, "tradestats", t, d, TS_COLS) for t, d in tasks]
        for f in as_completed(futs):
            n = f.result()
            total_rows += n
            done += 1
            if done % 500 == 0 or done == len(tasks):
                elapsed = time.time() - t0
                rate = done / elapsed
                eta = (len(tasks) - done) / rate if rate > 0 else 0
                print(f"TradeStats: {done}/{len(tasks)} tasks, {total_rows} rows, {rate:.1f}/s, ETA {eta:.0f}s", file=sys.stderr)
    print(f"TradeStats DONE: {total_rows} rows in {time.time()-t0:.0f}s")

    # OBStats
    tasks_obs = [(t, d) for t in tickers for d in dates]
    print(f"Total OBStats tasks: {len(tasks_obs)}", file=sys.stderr)
    done = 0
    total_rows = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(fetch_one, "obstats", t, d, OBS_COLS) for t, d in tasks_obs]
        for f in as_completed(futs):
            n = f.result()
            total_rows += n
            done += 1
            if done % 500 == 0 or done == len(tasks_obs):
                elapsed = time.time() - t0
                rate = done / elapsed
                eta = (len(tasks_obs) - done) / rate if rate > 0 else 0
                print(f"OBStats: {done}/{len(tasks_obs)} tasks, {total_rows} rows, {rate:.1f}/s, ETA {eta:.0f}s", file=sys.stderr)
    print(f"OBStats DONE: {total_rows} rows in {time.time()-t0:.0f}s")

    print(f"\\nALL DONE! Total rows: {total_rows}")
PYTHON_SCRIPT

echo "Script created, launching..."
cd /home/user/projects/TQA-MOEX
python3 /tmp/algopack_loader_final.py 2>&1