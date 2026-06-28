#!/usr/bin/env python3
"""Phase 2: Find correct column names and complete mapping."""

import clickhouse_connect

HOST = '10.0.0.60'
DB = 'moex'

client = clickhouse_connect.get_client(host=HOST, database=DB)

def q(sql, label):
    print(f"\n--- {label} ---")
    try:
        rows = client.query(sql).result_rows
        print(f"  -> {len(rows)} rows")
        return rows
    except Exception as e:
        print(f"  ERROR: {e}")
        return []

# Check full schemas of prices_5m and prices_5m_oi
for tbl in ['prices_5m', 'prices_5m_oi', 'futoi']:
    rows = q(f"SELECT name, type FROM system.columns WHERE database='moex' AND table='{tbl}'", f"{tbl} ALL columns")
    for r in rows:
        print(f"  {r[0]:20s} {r[1]}")

# Now try to find the actual ticker column in prices_5m and prices_5m_oi
for tbl in ['prices_5m', 'prices_5m_oi']:
    cols = q(f"SELECT name FROM system.columns WHERE database='moex' AND table='{tbl}'", f"{tbl} column names")
    names = [r[0] for r in cols]
    print(f"  Columns: {names}")
    for col in names:
        try:
            rows = client.query(f"SELECT DISTINCT {col} FROM {tbl} LIMIT 20")
            vals = [r[0] for r in rows.result_rows]
            # Check if values look like tickers (short strings like 'Si', 'AF', etc.)
            str_vals = [str(v) for v in vals if v is not None]
            if str_vals:
                sample = ', '.join(str_vals[:10])
                print(f"    {col}: sample values = {sample}")
        except Exception as e:
            pass

# Check futoi columns too
cols = q(f"SELECT name FROM system.columns WHERE database='moex' AND table='futoi'", "futoi column names")
names = [r[0] for r in cols]
print(f"  Columns: {names}")
for col in names:
    try:
        rows = client.query(f"SELECT DISTINCT {col} FROM futoi LIMIT 20")
        vals = [r[0] for r in rows.result_rows]
        str_vals = [str(v) for v in vals if v is not None]
        if str_vals:
            sample = ', '.join(str_vals[:10])
            print(f"    {col}: sample values = {sample}")
    except Exception as e:
        pass

client.close()
