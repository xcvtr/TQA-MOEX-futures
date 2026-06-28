#!/usr/bin/env python3
"""
MOEX Session Pattern Testing: Open Drive, Lunch Reversal, Close Sweep
Tests on Si, GAZR (GZ proxy), BR (CR proxy - Brent crude)
Data: 2024-10-01 to today
"""

import clickhouse_connect
import pandas as pd
import numpy as np
from datetime import datetime, date
import sys

# === CONFIG ===
CLICKHOUSE_HOST = '10.0.0.60'
CLICKHOUSE_PORT = 8123
DB = 'moex'
TABLE = 'moex.tradestats_fo'

TICKERS = {
    'Si': 'Si',
    'GZ': 'GAZR',  # proxy: Gazprom futures
    'CR': 'BR',    # proxy: Brent crude futures
}

START_DATE = '2024-10-01'
END_DATE = date.today().isoformat()

BAR_MINUTES = 10
FORWARD_BARS = [3, 6, 12]

# === HELPERS ===

def get_client():
    return clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT, database=DB)

def fetch_bars(client, ticker, start, end):
    """Fetch 10-min OHLCV bars for a ticker."""
    query = f"""
    SELECT
        toStartOfInterval(SYSTIME, INTERVAL {BAR_MINUTES} MINUTE, 'Europe/Moscow') AS bar_time,
        any(pr_open) AS open,
        max(pr_high) AS high,
        min(pr_low) AS low,
        argMax(pr_close, SYSTIME) AS close,
        sum(vol) AS volume
    FROM {TABLE}
    WHERE asset_code = '{ticker}'
      AND SYSTIME >= '{start}'
      AND SYSTIME < '{end}'
    GROUP BY bar_time
    ORDER BY bar_time
    """
    result = client.query(query)
    df = pd.DataFrame(
        result.result_rows,
        columns=['bar_time', 'open', 'high', 'low', 'close', 'volume']
    )
    df['bar_time'] = pd.to_datetime(df['bar_time'])
    df['hour'] = df['bar_time'].dt.hour
    df['minute'] = df['bar_time'].dt.minute
    df['date'] = df['bar_time'].dt.date
    df['ticker'] = ticker
    for col in ['open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
    return df

def compute_forward_returns(df):
    """Compute forward returns for 3, 6, 12 bars."""
    for n in FORWARD_BARS:
        df[f'fwd_return_{n}'] = df['close'].shift(-n) / df['close'] - 1
    return df

def safe_wr(series):
    """Win rate: fraction of positive values."""
    if len(series) == 0:
        return 0.0
    return (series > 0).sum() / len(series)

def safe_mean(series):
    return series.mean() if len(series) > 0 else 0.0

def net80(series):
    """80th percentile minus 20th percentile."""
    if len(series) < 5:
        return 0.0
    return np.percentile(series, 80) - np.percentile(series, 20)

def compute_stats(series):
    """Return WR, mean, net80 for a return series."""
    wr = safe_wr(series)
    mean = safe_mean(series) * 100  # as %
    n80 = net80(series) * 100
    count = len(series)
    return wr, mean, n80, count

# === PATTERN DEFINITIONS ===

def test_open_drive(df):
    """
    Open Drive (10:00-10:30): If volume in 10:00-10:30 > 2× avg(10min vol) 
    over last 5 days → momentum in direction of first 30 min price change.
    """
    results = {n: [] for n in FORWARD_BARS}
    
    open_drive_bars = df[(df['hour'] == 10) & (df['minute'] < 30)].copy()
    
    # Group by date: first 30 min volume and price change
    for d, grp in open_drive_bars.groupby('date'):
        grp = grp.sort_values('bar_time')
        if len(grp) < 1:
            continue
        
        total_vol = grp['volume'].sum()
        first_close = grp.iloc[0]['close']
        last_close = grp.iloc[-1]['close']
        price_change_pct = (last_close / first_close - 1)
        
        # Get bars from last 5 trading days for same hours (all bars, not just 10:00-10:30)
        d_ts = pd.Timestamp(d).tz_localize(df['bar_time'].dt.tz)
        lookback_start = d_ts - pd.Timedelta(days=10)
        
        last_5d_bars = df[(df['bar_time'] >= lookback_start) & 
                          (df['bar_time'] < d_ts)]
        
        # Filter to last 5 trading days (unique dates)
        unique_dates = last_5d_bars['date'].unique()
        if len(unique_dates) < 3:
            continue
        last_5d_dates = set(sorted(unique_dates)[-5:])
        last_5d_bars = last_5d_bars[last_5d_bars['date'].isin(last_5d_dates)]
        
        if len(last_5d_bars) < 10:  # need enough bars for baseline
            continue
        
        avg_10min_vol = last_5d_bars['volume'].mean()
        if avg_10min_vol <= 0:
            continue
        
        # Volume in first 30 min is ~3 10-min bars
        vol_ratio = total_vol / (avg_10min_vol * 3)
        
        if vol_ratio > 2.0:  # threshold: > 2x normal
            direction = 1 if price_change_pct > 0 else -1
            
            # Forward returns
            last_bar_idx = grp.index[-1]
            for n in FORWARD_BARS:
                if last_bar_idx + n < len(df):
                    ret = df.loc[last_bar_idx + n, 'close'] / df.loc[last_bar_idx, 'close'] - 1
                    signal_ret = ret * direction
                    results[n].append(signal_ret)
    
    return results

def test_lunch_reversal(df):
    """
    Lunch Reversal (13:00-14:00): If price grew from 10:00 to 13:00 → SHORT before 14:00.
    Enter at 13:00 bar, exit at forward bars.
    """
    results = {n: [] for n in FORWARD_BARS}
    
    for d, grp in df.groupby('date'):
        grp = grp.sort_values('bar_time')
        
        morning_bars = grp[(grp['hour'] == 10) & (grp['minute'] < 60)]
        lunch_entry_bars = grp[(grp['hour'] == 13) & (grp['minute'] < 60)]
        
        if len(morning_bars) < 1 or len(lunch_entry_bars) < 1:
            continue
        
        morning_open = morning_bars.iloc[0]['close']
        morning_close = morning_bars.iloc[-1]['close']
        
        # Price change from 10:00 to 13:00
        pre_13_price = lunch_entry_bars.iloc[0]['close']
        price_change = (pre_13_price / morning_open - 1)
        
        if price_change > 0.001:  # price grew → SHORT
            entry_idx = lunch_entry_bars.index[0]
            direction = -1  # SHORT
        elif price_change < -0.001:  # price fell → LONG
            entry_idx = lunch_entry_bars.index[0]
            direction = 1
        else:
            continue  # flat, no signal
        
        for n in FORWARD_BARS:
            if entry_idx + n < len(df):
                entry_close = df.loc[entry_idx, 'close']
                exit_close = df.loc[entry_idx + n, 'close']
                ret = (exit_close / entry_close - 1) * direction
                results[n].append(ret)
    
    return results

def test_close_sweep(df):
    """
    Close Sweep (18:00-18:45): If volume > 3× norm in last 45 min of main session → stop hunt.
    Enter at 18:45, exit forward.
    """
    results = {n: [] for n in FORWARD_BARS}
    
    for d, grp in df.groupby('date'):
        grp = grp.sort_values('bar_time')
        
        sweep_window = grp[(grp['hour'] == 18) & (grp['minute'] < 45)]
        if len(sweep_window) < 1:
            continue
        
        total_vol = sweep_window['volume'].sum()
        
        # Normal volume across all bars today (excluding the sweep window)
        non_sweep = grp[~((grp['hour'] == 18) & (grp['minute'] < 45))]
        if len(non_sweep) < 20:  # need enough bars for baseline
            continue
        
        avg_vol_10min = non_sweep['volume'].mean()
        if avg_vol_10min <= 0:
            continue
        
        # 45 min = ~4-5 bars depending on data
        num_bars = max(1, len(sweep_window))
        vol_ratio = total_vol / (avg_vol_10min * num_bars)
        
        if vol_ratio > 3.0:  # > 3x normal
            last_idx = sweep_window.index[-1]
            direction = 1  # stop hunt → mean reversion after sweep
            
            for n in FORWARD_BARS:
                if last_idx + n < len(df):
                    entry_close = df.loc[last_idx, 'close']
                    exit_close = df.loc[last_idx + n, 'close']
                    ret = (exit_close / entry_close - 1) * direction
                    results[n].append(ret)
    
    return results


# === MAIN ===

def main():
    client = get_client()
    
    print("=" * 100)
    print(f"MOEX SESSION PATTERN TEST: Open Drive · Lunch Reversal · Close Sweep")
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"Bar size: {BAR_MINUTES} min | Forward returns: {', '.join(f'{n} bars' for n in FORWARD_BARS)}")
    print("=" * 100)
    
    all_data = {}
    
    for short_name, ticker in TICKERS.items():
        print(f"\n📊 Fetching {short_name} ({ticker})...")
        df = fetch_bars(client, ticker, START_DATE, END_DATE)
        df = compute_forward_returns(df)
        print(f"   → {len(df)} bars from {df['bar_time'].min()} to {df['bar_time'].max()}")
        print(f"   → {df['date'].nunique()} trading days")
        all_data[short_name] = df
    
    print("\n" + "=" * 100)
    print("TEST RESULTS")
    print("=" * 100)
    
    patterns = {
        'Open Drive': test_open_drive,
        'Lunch Reversal': test_lunch_reversal,
        'Close Sweep': test_close_sweep,
    }
    
    all_rows = []
    
    for short_name in TICKERS:
        df = all_data[short_name]
        
        print(f"\n{'─' * 100}")
        print(f"🔷 {short_name} ({TICKERS[short_name]})")
        print(f"{'─' * 100}")
        
        for pattern_name, pattern_fn in patterns.items():
            print(f"\n  📍 {pattern_name}:")
            
            try:
                results = pattern_fn(df)
            except Exception as e:
                print(f"     ❌ Error: {e}")
                continue
            
            signals_total = len(results[FORWARD_BARS[0]])
            
            if signals_total == 0:
                print(f"     ⚠️  No signals detected")
                for n in FORWARD_BARS:
                    all_rows.append({
                        'Ticker': short_name,
                        'Pattern': pattern_name,
                        'ForwardBars': n,
                        'Signals': 0,
                        'WR': 0,
                        'Mean%': 0,
                        'Net80%': 0,
                        'Signal': 'NO SIGNAL'
                    })
                continue
            
            print(f"     Signals: {signals_total}")
            
            for n in FORWARD_BARS:
                returns = np.array(results[n])
                if len(returns) == 0:
                    wr, mean_val, n80, count = 0, 0, 0, 0
                    signal_str = 'NO SIGNAL'
                else:
                    wr, mean_val, n80, count = compute_stats(returns)
                    signal_str = '✅ SIGNAL' if wr >= 0.52 else '❌ NO SIGNAL'
                
                print(f"     ├─ {n} bar(s): WR={wr:.1%}  Mean={mean_val:+.3f}%  Net80={n80:.3f}%  [{signal_str}]")
                
                all_rows.append({
                    'Ticker': short_name,
                    'Pattern': pattern_name,
                    'ForwardBars': n,
                    'Signals': count,
                    'WR': round(wr, 4),
                    'Mean%': round(mean_val, 4),
                    'Net80%': round(n80, 4),
                    'Signal': signal_str
                })
    
    # Summary table
    print("\n" + "=" * 100)
    print("SUMMARY TABLE")
    print("=" * 100)
    
    summary_df = pd.DataFrame(all_rows)
    if len(summary_df) > 0:
        print(summary_df.to_string(index=False))
        
        # Save to CSV
        csv_path = f"/home/user/session_patterns_results.csv"
        summary_df.to_csv(csv_path, index=False)
        print(f"\n📁 Saved to {csv_path}")
    
    print("\n✅ Done!")

if __name__ == '__main__':
    main()
