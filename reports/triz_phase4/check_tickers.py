#!/usr/bin/env python3
"""Check which tickers have data to determine actual N"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../..')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# From megagrid.py GO_MAP and TICKERS:
GO_MAP = {'RI':27034,'GL':1352,'USDRUBF':11186,'AF':673,'BR':17228,'IMOEXF':2596,'CC':506,
          'NM':256,'PD':24487,'SV':12960,'VB':1556,'GD':32003,'SR':6620,'LK':11606,'PT':31749,
          'Si':12330,'Eu':14478,'CNYRUBF':875,'CR':17200,'NG':8027,'MX':4133,'AL':728,'RN':3152}

TICKERS = ['CC', 'NM', 'PD', 'SV', 'VB', 'GD', 'SR', 'LK', 'PT', 'Si', 'Eu', 'CNYRUBF', 'CR', 'NG', 'MX', 'AL', 'RN']

print("Checking tickers from TICKERS list:")
for sym in TICKERS:
    d_rows = ch.query("""
        SELECT toDate(p.time) as d
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': sym}).result_rows
    ok = len(d_rows) >= 60
    print(f"  {sym:>10}: {len(d_rows):>4} days {'OK' if ok else 'SHORT'}")

print("\nChecking ALL GO_MAP symbols:")
for sym in GO_MAP:
    d_rows = ch.query("""
        SELECT toDate(p.time) as d
        FROM moex.prices_5m p INNER JOIN moex.prices_5m_oi o ON p.symbol=o.symbol AND p.time=o.time
        WHERE p.symbol=%(t)s AND p.time>='2024-01-01' AND p.time<='2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': sym}).result_rows
    ok = len(d_rows) >= 60
    if ok:
        print(f"  {sym:>10}: {len(d_rows):>4} days ACTIVE")
