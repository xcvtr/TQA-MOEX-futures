#!/usr/bin/env python3
"""
Diagnostic: Check z-score distribution and signal frequency for MOEX futures.
"""

import pandas as pd
import numpy as np
import json
import urllib.request
import urllib.parse
from collections import defaultdict

CLICKHOUSE_HOST = "10.0.0.60"
CLICKHOUSE_PORT = "8123"
DB = "moex"
START_DATE = "2024-10-01"
END_DATE = "2026-06-28"

def ch_query(query):
    params = urllib.parse.urlencode({'database': DB, 'default_format': 'JSONEachRow'})
    url = f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}/?{params}"
    data = query.encode('utf-8')
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=120) as resp:
        text = resp.read().decode('utf-8')
        if not text.strip():
            return []
        lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
        return [json.loads(l) for l in lines]

def zscore(series):
    mean = series.rolling(20, min_periods=20).mean()
    std = series.rolling(20, min_periods=20).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)

# Test a few key assets
test_tickers = ['SR', 'Si', 'VB', 'AL', 'BR', 'GZ', 'LK', 'RN', 'GK', 'CH', 'GD']

for ticker in test_tickers:
    # Get best_secid + hhi + price data
    rows = ch_query(f"""
        SELECT bs.tradedate, bs.best_secid, h.value as hhi, f.close
        FROM moex._daily_best_secid bs
        JOIN moex.hi2_fo h ON h.secid = bs.best_secid AND h.tradedate = bs.tradedate AND h.metric = 'hhi_agressive'
        JOIN moex.futures_history f ON f.secid = bs.best_secid AND f.date = bs.tradedate
        WHERE bs.ticker = '{ticker}'
          AND bs.tradedate >= '{START_DATE}' AND bs.tradedate <= '{END_DATE}'
        ORDER BY bs.tradedate
    """)
    
    if len(rows) < 25:
        print(f"{ticker}: only {len(rows)} bars, skipping")
        continue
    
    df = pd.DataFrame(rows)
    df['date'] = df['tradedate']
    df['hhi'] = df['hhi'].astype(float)
    df['close'] = df['close'].astype(float)
    df = df.sort_values('date').reset_index(drop=True)
    
    df['z_hhi'] = zscore(df['hhi'])
    df['z_price'] = zscore(df['close'])
    
    # Count signals
    valid = df.dropna(subset=['z_hhi', 'z_price'])
    
    n_hhi_above_15 = (valid['z_hhi'] > 1.5).sum()
    n_price_below_m1 = (valid['z_price'] < -1.0).sum()
    n_price_above_1 = (valid['z_price'] > 1.0).sum()
    n_long_signal = ((valid['z_hhi'] > 1.5) & (valid['z_price'] < -1.0)).sum()
    n_short_signal = ((valid['z_hhi'] > 1.5) & (valid['z_price'] > 1.0)).sum()
    
    print(f"\n{ticker}: {len(valid)} valid bars")
    print(f"  z_HHI > 1.5: {n_hhi_above_15} ({n_hhi_above_15/len(valid)*100:.1f}%)")
    print(f"  z_price < -1.0: {n_price_below_m1} ({n_price_below_m1/len(valid)*100:.1f}%)")
    print(f"  z_price > 1.0: {n_price_above_1} ({n_price_above_1/len(valid)*100:.1f}%)")
    print(f"  LONG signals: {n_long_signal}")
    print(f"  SHORT signals: {n_short_signal}")
    print(f"  z_HHI percentiles: 50%={df['z_hhi'].median():.2f}, 75%={df['z_hhi'].quantile(0.75):.2f}, 90%={df['z_hhi'].quantile(0.90):.2f}, 95%={df['z_hhi'].quantile(0.95):.2f}, 99%={df['z_hhi'].quantile(0.99):.2f}")
