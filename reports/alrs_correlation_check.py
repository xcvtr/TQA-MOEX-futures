#!/usr/bin/env python3
"""Check correlation of ALRS with BRENT oil and other macro factors."""
import psycopg2
import pandas as pd
import numpy as np
import requests
import json
from datetime import datetime

# 1. Get ALRS daily data already computed
alrs = pd.read_csv('/home/user/alrs_short_analysis.csv', parse_dates=['date'])
alrs.set_index('date', inplace=True)

# 2. Try to get BRENT oil price history from a free API
print("Fetching BRENT oil price data (free source)...")
# Using EIA API or alternative - let's try a simple approach with investing.com mirror
oil_prices = {}
try:
    # Try to read from any available parquet file in DB
    conn = psycopg2.connect(host="10.0.0.60", dbname="moex", user="postgres", password="postgres")
    
    # Check if there's an oil/futures table
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema='public' AND table_name LIKE '%oil%' OR table_name LIKE '%brent%' OR table_name LIKE '%commodit%'
    """)
    tables = cur.fetchall()
    print(f"Available tables matching oil/commodity: {tables}")
    
    # Check moex_prices_5m for other symbols
    cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m WHERE symbol LIKE '%BR%' OR symbol LIKE '%OIL%' OR symbol LIKE '%BZ%' ORDER BY symbol")
    symbols = cur.fetchall()
    print(f"Available oil/energy symbols in DB: {symbols}")

    # Also look for any macro/economic data tables
    cur.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema='public' AND table_name NOT LIKE '%pg_%' AND table_name NOT LIKE 'sql_%'
        ORDER BY table_name
    """)
    all_tables = [t[0] for t in cur.fetchall()]
    print(f"\nAll public tables: {all_tables}")
    
except Exception as e:
    print(f"DB error: {e}")
finally:
    try:
        conn.close()
    except:
        pass

# 3. Since we may not have oil data in DB, let's try web API
print("\n\nAttempting to fetch BRENT data from web...")
try:
    # Using a simple CSV source
    url = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?period1=1672531200&period2=1748217600&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        print("Got BRENT data from Yahoo Finance")
        # Parse
        timestamps = data['chart']['result'][0]['timestamp']
        quotes = data['chart']['result'][0]['indicators']['quote'][0]
        closes = quotes['close']
        
        oil_df = pd.DataFrame({
            'date': pd.to_datetime(timestamps, unit='s'),
            'brent_close': closes
        }).dropna()
        oil_df.set_index('date', inplace=True)
        
        # Merge with ALRS
        merged = alrs[['close']].join(oil_df, how='inner')
        if len(merged) > 10:
            corr = merged['close'].corr(merged['brent_close'])
            print(f"\nALRS vs BRENT oil correlation: {corr:.4f}")
            print(f"  Overlapping periods: {len(merged)} days")
            
            # Also check correlation of returns
            merged['alrs_ret'] = merged['close'].pct_change()
            merged['brent_ret'] = merged['brent_close'].pct_change()
            ret_corr = merged['alrs_ret'].corr(merged['brent_ret'])
            print(f"  Return correlation: {ret_corr:.4f}")
        else:
            print(f"Too few overlapping points: {len(merged)}")
    else:
        print(f"Yahoo Finance returned {resp.status_code}")
except Exception as e:
    print(f"Web fetch error: {e}")

print("\n\n--- ANALYSIS COMPLETE ---")
