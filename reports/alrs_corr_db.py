#!/usr/bin/env python3
"""Correlate ALRS with BRENT oil futures from MOEX database."""
import psycopg2
import pandas as pd
import numpy as np

conn = psycopg2.connect(host="10.0.0.60", dbname="moex", user="postgres", password="postgres")

# Get ALRS daily data
query_al = """
    SELECT time::date as date, close
    FROM moex_prices_5m
    WHERE symbol = 'AL' AND time >= '2023-01-01' AND time < '2026-06-01'
    ORDER BY time
"""
df_al = pd.read_sql(query_al, conn)
df_al = df_al.groupby('date')['close'].last().reset_index()

# Get BRENT daily data  
query_br = """
    SELECT time::date as date, close
    FROM moex_prices_5m
    WHERE symbol = 'BR' AND time >= '2023-01-01' AND time < '2026-06-01'
    ORDER BY time
"""
df_br = pd.read_sql(query_br, conn)
df_br = df_br.groupby('date')['close'].last().reset_index()

conn.close()

print(f"ALRS data: {len(df_al)} days, {df_al['date'].min()} to {df_al['date'].max()}")
print(f"BRENT data: {len(df_br)} days, {df_br['date'].min()} to {df_br['date'].max()}")
print(f"ALRS price range: {df_al['close'].min():.0f} - {df_al['close'].max():.0f}")
print(f"BRENT price range: {df_br['close'].min():.0f} - {df_br['close'].max():.0f}")

# Merge
merged = pd.merge(df_al, df_br, on='date', suffixes=('_alrs', '_brent'), how='inner')
print(f"\nCommon trading days: {len(merged)}")

if len(merged) > 10:
    # Price correlation
    corr_price = merged['close_alrs'].corr(merged['close_brent'])
    print(f"\nPrice correlation (ALRS vs BRENT): {corr_price:.4f}")
    
    # Return correlation
    merged['ret_alrs'] = merged['close_alrs'].pct_change()
    merged['ret_brent'] = merged['close_brent'].pct_change()
    corr_ret = merged['ret_alrs'].corr(merged['ret_brent'])
    print(f"Return correlation: {corr_ret:.4f}")
    
    # Rolling correlation (60-day)
    merged['rolling_corr'] = merged['ret_alrs'].rolling(60).corr(merged['ret_brent'])
    print(f"Rolling 60d correlation mean: {merged['rolling_corr'].mean():.4f}")
    print(f"Rolling 60d correlation std: {merged['rolling_corr'].std():.4f}")
    print(f"Rolling 60d correlation min: {merged['rolling_corr'].min():.4f}")
    print(f"Rolling 60d correlation max: {merged['rolling_corr'].max():.4f}")
    
    # Recent correlation (last 6 months)
    recent = merged.tail(120)
    corr_recent = recent['ret_alrs'].corr(recent['ret_brent'])
    print(f"\nLast 6 months return correlation: {corr_recent:.4f}")

    # Also check if BRENT has similar trend
    br_start = df_br['close'].iloc[0]
    br_end = df_br['close'].iloc[-1]
    al_start = df_al['close'].iloc[0]
    al_end = df_al['close'].iloc[-1]
    print(f"\n{'='*60}")
    print("TREND COMPARISON:")
    print(f"{'='*60}")
    print(f"ALRS: {al_start:.0f} -> {al_end:.0f} ({(al_end/al_start-1)*100:+.2f}%)")
    print(f"BRENT: {br_start:.0f} -> {br_end:.0f} ({(br_end/br_start-1)*100:+.2f}%)")

    # Export merged data
    merged.to_csv('/home/user/alrs_brent_correlation.csv', index=False, float_format='%.4f')
    print(f"\nCorrelation data saved to /home/user/alrs_brent_correlation.csv")

    # Summary stats for ALRS short strategy vs BRENT
    print(f"\n{'='*60}")
    print("CORRELATION ANALYSIS SUMMARY:")
    print(f"{'='*60}")
    print(f"ALRS is {'positively' if corr_price > 0 else 'negatively'} correlated with BRENT oil (r={corr_price:.3f})")
    print(f"Returns are {'positively' if corr_ret > 0 else 'negatively'} correlated (r={corr_ret:.3f})")
    if abs(corr_ret) > 0.3:
        print(f"  -> Moderate to strong correlation")
    elif abs(corr_ret) > 0.1:
        print(f"  -> Weak correlation")
    else:
        print(f"  -> No significant correlation")
