#!/home/user/venvs/tqa/main/bin/python
"""
TQA Stage 2b — Economic Event Correlation.
Загружает результаты rolling_cluster.py и проверяет,
какие экономические события совпадают с убыточными трейдами.
"""

import json, sys, warnings
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

DB = dict(host="10.0.0.60", port=5432, dbname="forex", user="postgres", password="postgres")

SYM_COUNTRY = {
    "audjpy": ("AU", "JP"), "audusd": ("AU", "US"), "euraud": ("EU", "AU"),
    "eurgbp": ("EU", "GB"), "eurjpy": ("EU", "JP"), "eurusd": ("EU", "US"),
    "gbpjpy": ("GB", "JP"), "gbpusd": ("GB", "US"), "nzdusd": ("NZ", "US"),
    "usdcad": ("US", "CA"), "usdchf": ("US", "CH"), "usdjpy": ("US", "JP"),
    "xauusd": ("US",),
}


def load_economic_events(conn, start, end):
    """Load economic events for the period with country filter."""
    q = f"""
    SELECT event_time AT TIME ZONE 'UTC' as event_time,
           country_code, name, event_code,
           actual_value, forecast_value, prev_value,
           importance
    FROM economic_calendar
    WHERE event_time >= '{start}' AND event_time < '{end}'
      AND importance >= 1
    ORDER BY event_time
    """
    df = pd.read_sql(q, conn)
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    return df


def correlation_analysis(stage2_path, conn, start, end):
    """For each losing trade, find economic events in the ±2 day window."""
    with open(stage2_path) as f:
        data = json.load(f)

    events = load_economic_events(conn, start, end)
    all_trades = []
    for sym, r in data.get("symbols", {}).items():
        tl = r.get("trades_list", [])
        if isinstance(tl, list):
            for t in tl:
                all_trades.append({**t, "symbol": sym})

    if not all_trades:
        print("No trades found")
        return

    losers = [t for t in all_trades if t.get("pnl", 0) <= 0]
    winners = [t for t in all_trades if t.get("pnl", 0) > 0]
    print(f"Total trades: {len(all_trades)}, losers: {len(losers)}, winners: {len(winners)}")

    # For each loser: check which events happened in the ±2 day window around exit_time
    event_hits = Counter()
    loser_details = []

    for t in losers:
        exit_ts = t.get("exit_time", "")
        if not exit_ts:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_ts).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            exit_dt = datetime.strptime(exit_ts[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)

        w_start = exit_dt - timedelta(days=2)
        w_end = exit_dt + timedelta(days=2)

        # Get countries for this symbol
        countries = SYM_COUNTRY.get(t.get("symbol", ""), ())
        if not countries:
            continue

        # Find events in window for relevant countries
        for _, ev in events.iterrows():
            if ev["country_code"] not in countries:
                continue
            if w_start <= ev["event_time"] <= w_end:
                hours_diff = (ev["event_time"] - exit_dt).total_seconds() / 3600
                event_hits[ev["event_code"]] += 1
                loser_details.append({
                    "symbol": t["symbol"],
                    "exit_time": exit_ts,
                    "pnl": t["pnl"],
                    "event_time": str(ev["event_time"])[:19],
                    "event_name": ev["name"],
                    "event_code": ev["event_code"],
                    "country": ev["country_code"],
                    "hours_from_exit": round(hours_diff, 1),
                })

    print(f"\nTop events associated with losing trades:")
    for ev_code, count in event_hits.most_common(20):
        names = set(d["event_name"] for d in loser_details if d.get("event_code", ev_code) == ev_code)
        if not names:
            # Get name from events table
            names = set(events[events["event_code"] == ev_code]["name"].unique())
        name_str = ", ".join(list(names)[:2])
        countries = set(d["country"] for d in loser_details if d.get("event_code", ev_code) == ev_code)
        print(f"  {ev_code:40s} x{count:3d}  [{', '.join(countries)}]  {name_str[:50]}")

    return {"event_hits": event_hits, "loser_details": loser_details}


if __name__ == "__main__":
    stage2 = sys.argv[1] if len(sys.argv) > 1 else "/home/user/.hermes/cache/screenshots/tqa/stage2_2025-01-01_2025-12-31.json"
    conn = psycopg2.connect(**DB)
    correlation_analysis(stage2, conn, "2025-01-01", "2025-12-31")
    conn.close()
