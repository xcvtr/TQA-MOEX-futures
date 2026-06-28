#!/usr/bin/env python3
"""
ALRS always-short strategy analysis.
Hypothesis: shorting ALRS continuously from 2023-01-03 to 2026-05-31 yields +59%.
"""
import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, date
import json

DB_HOST = "10.0.0.60"
DB_NAME = "moex"
DB_USER = "postgres"
DB_PASS = "postgres"

conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
cur = conn.cursor()

# --- 1. Get 5-min data for AL (ALRS futures) ---
print("Fetching 5-min data for AL...")
query = """
    SELECT time, open, high, low, close, volume
    FROM moex_prices_5m
    WHERE symbol = 'AL'
      AND time >= '2023-01-01'
      AND time < '2026-06-01'
    ORDER BY time
"""
df = pd.read_sql(query, conn)
conn.close()

print(f"Loaded {len(df)} rows of 5-min data")
print(f"Date range: {df['time'].min()} to {df['time'].max()}")
print(f"Price range: {df['low'].min():.2f} - {df['high'].max():.2f}")

# --- 2. Build daily OHLCV ---
df['date'] = df['time'].dt.date

daily = df.groupby('date').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last',
    'volume': 'sum'
}).reset_index()
daily.columns = ['date', 'open', 'high', 'low', 'close', 'volume']

print(f"\nDaily bars: {len(daily)}")
print(f"First: {daily.iloc[0]['date']} - Close: {daily.iloc[0]['close']:.2f}")
print(f"Last:  {daily.iloc[-1]['date']} - Close: {daily.iloc[-1]['close']:.2f}")

# --- 3. Short-only strategy simulation ---
# Daily return (short) = (close_prev - close) / close_prev
daily['short_return'] = 0.0
for i in range(1, len(daily)):
    daily.loc[daily.index[i], 'short_return'] = (
        daily.loc[daily.index[i-1], 'close'] - daily.loc[daily.index[i], 'close']
    ) / daily.loc[daily.index[i-1], 'close']

# Compound equity curve
daily['equity'] = (1 + daily['short_return']).cumprod()
cumulative_return = daily['equity'].iloc[-1] - 1

print(f"\n{'='*60}")
print(f"CUMULATIVE SHORT RETURN: {cumulative_return*100:.2f}%")
print(f"{'='*60}")

# Also calculate simple buy-and-hold (long) for comparison
daily['long_return'] = daily['close'].pct_change()
daily['long_equity'] = (1 + daily['long_return']).cumprod()
long_cumulative = daily['long_equity'].iloc[-1] - 1
print(f"Long buy-hold return: {long_cumulative*100:.2f}%")

# --- 4. Max drawdown ---
running_max = daily['equity'].cummax()
daily['drawdown'] = daily['equity'] / running_max - 1
max_dd = daily['drawdown'].min()
max_dd_date = daily.loc[daily['drawdown'].idxmin(), 'date']

print(f"\nMAX DRAWDOWN (short strategy): {max_dd*100:.2f}% on {max_dd_date}")

# --- 5. Annualized volatility ---
daily_vol = daily['short_return'].std()
annualized_vol = daily_vol * np.sqrt(252)
print(f"\nDaily volatility: {daily_vol*100:.2f}%")
print(f"Annualized volatility: {annualized_vol*100:.2f}%")

# Sharpe-like (assuming 0% risk-free)
sharpe = daily['short_return'].mean() / daily_vol * np.sqrt(252)
print(f"Sharpe ratio (short, rf=0): {sharpe:.2f}")

# --- 6. Monthly / Quarterly returns ---
daily['month'] = pd.to_datetime(daily['date']).dt.to_period('M')
daily['quarter'] = pd.to_datetime(daily['date']).dt.to_period('Q')
daily['year_num'] = pd.to_datetime(daily['date']).dt.year

# Monthly long returns
monthly_long = daily.groupby('month')['long_return'].sum()
monthly_short = daily.groupby('month')['short_return'].sum()

print(f"\n{'='*60}")
print("MONTHLY RETURNS (LONG):")
print(f"{'='*60}")
for period, ret in monthly_long.items():
    print(f"  {period}: {ret*100:+6.2f}%")

print(f"\n{'='*60}")
print("MONTHLY RETURNS (SHORT):")
print(f"{'='*60}")
for period, ret in monthly_short.items():
    print(f"  {period}: {ret*100:+6.2f}%")

# Quarterly long returns
quarterly_long = daily.groupby('quarter')['long_return'].sum()
print(f"\n{'='*60}")
print("QUARTERLY RETURNS (LONG):")
print(f"{'='*60}")
for period, ret in quarterly_long.items():
    print(f"  {period}: {ret*100:+6.2f}%")

# Yearly
yearly_long = daily.groupby('year_num')['long_return'].sum()
yearly_short = daily.groupby('year_num')['short_return'].sum()
print(f"\n{'='*60}")
print("YEARLY RETURNS:")
print(f"{'='*60}")
print(f"{'Year':<6} {'Long':>10} {'Short':>10}")
for yr in sorted(set(daily['year_num'])):
    yr_data = daily[daily['year_num'] == yr]
    long_yr = yr_data['long_return'].sum()
    short_yr = yr_data['short_return'].sum()
    print(f"  {yr:<6} {long_yr*100:+9.2f}% {short_yr*100:+9.2f}%")

# --- 7. Seasonality by month ---
daily['month_num'] = pd.to_datetime(daily['date']).dt.month
monthly_avg_long = daily.groupby('month_num')['long_return'].agg(['sum', 'mean', 'count'])
print(f"\n{'='*60}")
print("SEASONALITY - Long returns by calendar month:")
print(f"{'='*60}")
print(f"{'Month':<8} {'Sum':>10} {'Avg':>10} {'Count':>8}")
month_names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
for m in range(1, 13):
    row = monthly_avg_long.loc[m]
    print(f"  {month_names[m-1]:<8} {row['sum']*100:+9.2f}% {row['mean']*100:+9.2f}% {row['count']:>8}")

# --- 8. Quarterly seasonality ---
daily['quarter_num'] = pd.to_datetime(daily['date']).dt.quarter
quarterly_avg_long = daily.groupby('quarter_num')['long_return'].agg(['sum', 'mean', 'count'])
print(f"\n{'='*60}")
print("SEASONALITY - Long returns by quarter:")
print(f"{'='*60}")
print(f"{'Quarter':<8} {'Sum':>10} {'Avg':>10} {'Count':>8}")
for q in range(1, 5):
    row = quarterly_avg_long.loc[q]
    print(f"  Q{q:<7} {row['sum']*100:+9.2f}% {row['mean']*100:+9.2f}% {row['count']:>8}")

# --- 9. Dividend season check (May-July) ---
print(f"\n{'='*60}")
print("DIVIDEND SEASON CHECK (May-July):")
print(f"{'='*60}")
dividend_months = [5, 6, 7]
non_dividend_months = [m for m in range(1, 13) if m not in dividend_months]
div_data = daily[daily['month_num'].isin(dividend_months)]
non_div_data = daily[~daily['month_num'].isin(dividend_months)]
print(f"  May-Jul avg long return: {div_data['long_return'].mean()*100:.3f}% per day")
print(f"  Other months avg long return: {non_div_data['long_return'].mean()*100:.3f}% per day")
print(f"  May-Jul total long return: {div_data['long_return'].sum()*100:.2f}%")
print(f"  Other months total long return: {non_div_data['long_return'].sum()*100:.2f}%")

# --- 10. Positive months for long ---
print(f"\n{'='*60}")
print("MONTHS WITH POSITIVE LONG RETURN:")
print(f"{'='*60}")
positive_months = monthly_long[monthly_long > 0]
negative_months = monthly_long[monthly_long <= 0]
print(f"  Positive months: {len(positive_months)} / {len(monthly_long)} ({len(positive_months)/len(monthly_long)*100:.1f}%)")
print(f"  Negative months: {len(negative_months)} / {len(monthly_long)} ({len(negative_months)/len(monthly_long)*100:.1f}%)")

# --- 11. Summary statistics ---
print(f"\n{'='*60}")
print("SUMMARY STATISTICS (SHORT STRATEGY):")
print(f"{'='*60}")
print(f"  Period:           {daily['date'].min()} to {daily['date'].max()}")
print(f"  Trading days:     {len(daily)}")
print(f"  Short return:     {cumulative_return*100:.2f}%")
print(f"  Long return:      {long_cumulative*100:.2f}%")
print(f"  Max drawdown:     {max_dd*100:.2f}% on {max_dd_date}")
print(f"  Annualized vol:   {annualized_vol*100:.2f}%")
print(f"  Sharpe (rf=0):    {sharpe:.2f}")
print(f"  Avg daily return: {daily['short_return'].mean()*100:.4f}%")
print(f"  Win days:         {(daily['short_return']>0).sum()}/{len(daily)} ({(daily['short_return']>0).mean()*100:.1f}%)")
print(f"  Best day:         {daily['short_return'].max()*100:.2f}%")
print(f"  Worst day:        {daily['short_return'].min()*100:.2f}%")

# --- 12. Save equity curve to CSV ---
daily[['date', 'open', 'high', 'low', 'close', 'volume', 
       'short_return', 'equity', 'long_return', 'long_equity', 'drawdown']].to_csv(
    '/home/user/alrs_short_analysis.csv', index=False, float_format='%.6f')
print(f"\nFull data saved to /home/user/alrs_short_analysis.csv")

# --- 13. Hypothesis check ---
print(f"\n{'='*60}")
print("HYPOTHESIS CHECK: 'Short ALRS -> +59% in 3 years'")
print(f"{'='*60}")
hypothesis_return = 0.59
if abs(cumulative_return - hypothesis_return) < 0.05:
    print(f"  ✅ HYPOTHESIS CONFIRMED: {cumulative_return*100:.2f}% ≈ 59%")
elif cumulative_return > hypothesis_return:
    print(f"  🔥 HYPOTHESIS EXCEEDED: {cumulative_return*100:.2f}% > 59%")
else:
    print(f"  ❌ HYPOTHESIS NOT CONFIRMED: {cumulative_return*100:.2f}% vs 59%")
    print(f"  Difference: {abs(cumulative_return - hypothesis_return)*100:.2f} pp")
print(f"{'='*60}")
