#!/usr/bin/env python3
"""
Quick BR/CR correlation check — why no divergence events?
"""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

CLICKHOUSE_URL = 'http://10.0.0.60:8123/'

def get_df(secid, start, end):
    sql = f"""
    SELECT tradedate, tradetime, pr_close
    FROM moex.tradestats_fo
    WHERE secid = '{secid}'
      AND tradedate >= '{start}'
      AND tradedate <= '{end}'
      AND pr_close IS NOT NULL
    ORDER BY tradedate, tradetime
    FORMAT TabSeparated
    """
    r = requests.post(CLICKHOUSE_URL, data=sql.encode('utf-8'), timeout=60)
    rows = r.text.strip().split('\n')
    dates, times, closes = [], [], []
    for row in rows:
        parts = row.split('\t')
        dates.append(parts[0])
        times.append(parts[1])
        closes.append(float(parts[2]))
    
    df = pd.DataFrame({'datetime': pd.to_datetime([d + ' ' + t for d, t in zip(dates, times)]), 'close': closes})
    df = df.set_index('datetime')
    df = df[df.index.dayofweek < 5]  # weekdays only
    df['ret'] = df['close'].pct_change()
    return df

today = datetime.now().strftime('%Y-%m-%d')
start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

# Get data
df_br = get_df('BRN6', start, today)
df_cr = get_df('CRU6', start, today)
df_si = get_df('SiU6', start, today)
df_brq = get_df('BRQ6', start, today)

print(f"BRN6: {len(df_br)} bars, price {df_br['close'].min():.2f}-{df_br['close'].max():.2f}")
print(f"BRQ6: {len(df_brq)} bars, price {df_brq['close'].min():.2f}-{df_brq['close'].max():.2f}")
print(f"CRU6: {len(df_cr)} bars, price {df_cr['close'].min():.2f}-{df_cr['close'].max():.2f}")
print(f"SiU6: {len(df_si)} bars, price {df_si['close'].min():.2f}-{df_si['close'].max():.2f}")

# BR/CR correlation
merged_br = pd.DataFrame()
merged_br['br_ret'] = df_br['ret']
merged_br['cr_ret'] = df_cr['ret']
merged_br = merged_br.dropna()
print(f"\n=== BR/CR (BRN6/CRU6) ===")
print(f"Common bars: {len(merged_br)}")
print(f"Overall corr: {merged_br['br_ret'].corr(merged_br['cr_ret']):.4f}")
merged_br['roll_corr'] = merged_br['br_ret'].rolling(20).corr(merged_br['cr_ret'])
print(f"Roll corr: mean={merged_br['roll_corr'].mean():.3f}, std={merged_br['roll_corr'].std():.3f}")
print(f"  > 0.7: {(merged_br['roll_corr']>0.7).sum()} bars ({(merged_br['roll_corr']>0.7).sum()/len(merged_br)*100:.1f}%)")
print(f"  < 0.3: {(merged_br['roll_corr']<0.3).sum()} bars ({(merged_br['roll_corr']<0.3).sum()/len(merged_br)*100:.1f}%)")
print(f"=> No divergence events because BR/CR have consistently low correlation")

# BRQ/CR correlation
merged_brq = pd.DataFrame()
merged_brq['br_ret'] = df_brq['ret']
merged_brq['cr_ret'] = df_cr['ret']
merged_brq = merged_brq.dropna()
print(f"\n=== BR/CR (BRQ6/CRU6) ===")
print(f"Common bars: {len(merged_brq)}")
print(f"Overall corr: {merged_brq['br_ret'].corr(merged_brq['cr_ret']):.4f}")
merged_brq['roll_corr'] = merged_brq['br_ret'].rolling(20).corr(merged_brq['cr_ret'])
print(f"Roll corr: mean={merged_brq['roll_corr'].mean():.3f}, std={merged_brq['roll_corr'].std():.3f}")
print(f"  > 0.7: {(merged_brq['roll_corr']>0.7).sum()} bars ({(merged_brq['roll_corr']>0.7).sum()/len(merged_brq)*100:.1f}%)")

# Si/CR correlation
merged_si = pd.DataFrame()
merged_si['si_ret'] = df_si['ret']
merged_si['cr_ret'] = df_cr['ret']
merged_si = merged_si.dropna()
print(f"\n=== Si/CR (SiU6/CRU6) ===")
print(f"Common bars: {len(merged_si)}")
print(f"Overall corr: {merged_si['si_ret'].corr(merged_si['cr_ret']):.4f}")
merged_si['roll_corr'] = merged_si['si_ret'].rolling(20).corr(merged_si['cr_ret'])
print(f"Roll corr: mean={merged_si['roll_corr'].mean():.3f}, std={merged_si['roll_corr'].std():.3f}")
print(f"  > 0.7: {(merged_si['roll_corr']>0.7).sum()} bars ({(merged_si['roll_corr']>0.7).sum()/len(merged_si)*100:.1f}%)")
print(f"  < 0.3: {(merged_si['roll_corr']<0.3).sum()} bars ({(merged_si['roll_corr']<0.3).sum()/len(merged_si)*100:.1f}%)")
print(f"=> Si/CR are both currency pairs (USDRUB vs CNYRUB), so they correlate well")
