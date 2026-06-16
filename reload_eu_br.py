#!/usr/bin/env python3
"""Delete and reload Eu/BR data — ClickHouse version."""
import os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, "/home/user/projects/TQA-MOEX")
from config import CH_HOST, CH_PORT, CH_DB
import clickhouse_connect

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

for sym in ("Eu", "BR"):
    # Count before
    row_before = ch.query("SELECT count() FROM moex.prices_5m WHERE symbol = {s:String}",
                          parameters={"s": sym}).result_rows
    before = row_before[0][0] if row_before else 0

    # Delete — CH не поддерживает DELETE, делаем через ALTER TABLE DELETE
    ch.query(f"ALTER TABLE moex.prices_5m DELETE WHERE symbol = '{sym}'")
    time.sleep(1)

    # Count after
    row_after = ch.query("SELECT count() FROM moex.prices_5m WHERE symbol = {s:String}",
                         parameters={"s": sym}).result_rows
    after = row_after[0][0] if row_after else 0

    print(f"{sym}: deleted {before - after} rows (before={before}, after={after})")

print("\nNow re-running price_history_5m.py...")
