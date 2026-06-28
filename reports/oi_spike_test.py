#!/usr/bin/env python3
"""
Intraday OI Spike Test for MOEX Futures
HTTP-based ClickHouse queries (port 8123)
"""

import pandas as pd
import numpy as np
from scipy import stats
import requests
import warnings
import io
from datetime import datetime

warnings.filterwarnings('ignore')

# ── Config ──────────────────────────────────────────────────────────────────
CLICKHOUSE_URL = 'http://10.0.0.60:8123'
DATABASE = 'moex'
TICKERS = ['Si', 'GZ', 'CR', 'RB']
START_DATE = '2024-10-01'
END_DATE = datetime.now().strftime('%Y-%m-%d')
Z_THRESHOLD = 3.0
ROLLING_WINDOW = 30
FORWARD_BARS = [3, 6, 12]
MIN_EVENTS = 5

def ch_query(sql):
    """Execute ClickHouse SQL via HTTP interface."""
    response = requests.post(
        CLICKHOUSE_URL,
        params={'database': DATABASE},
        data=sql.encode('utf-8'),
        timeout=60
    )
    response.raise_for_status()
    if not response.text.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(response.text), sep='\t')

def query_5m_bars(ticker):
    """Query 5-minute bars from ClickHouse."""
    sql = f"""
    SELECT
        toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
        argMax(pr_close, SYSTIME) as prc,
        argMax(oi_close, SYSTIME) as oi
    FROM moex.tradestats_fo
    WHERE secid LIKE '{ticker}%'
      AND SYSTIME >= '{START_DATE}'
      AND SYSTIME < '{END_DATE}'
      AND oi_close > 0
    GROUP BY bt
    ORDER BY bt
    FORMAT TabSeparatedWithNames
    """
    try:
        df = ch_query(sql)
    except Exception as e:
        print(f"  ❌ Query failed for {ticker}: {e}")
        return None
    
    if df.empty or len(df) < 50:
        return None
    
    df['bt'] = pd.to_datetime(df['bt'])
    return df

def compute_oi_spikes(df):
    """Compute OI change, z-score, and classify events."""
    df = df.copy()
    df['oi_change'] = df['oi'] / df['oi'].shift(1)
    df['log_oi_change'] = np.log(df['oi_change'])
    
    # Rolling z-score over 30 bars
    roll_mean = df['log_oi_change'].rolling(ROLLING_WINDOW, min_periods=ROLLING_WINDOW).mean()
    roll_std = df['log_oi_change'].rolling(ROLLING_WINDOW, min_periods=ROLLING_WINDOW).std()
    df['z_oi'] = (df['log_oi_change'] - roll_mean) / roll_std
    
    # Price change
    df['prc_change'] = df['prc'].pct_change()
    
    # Z-score direction
    df['oi_spike_up'] = df['z_oi'] > Z_THRESHOLD
    df['oi_spike_down'] = df['z_oi'] < -Z_THRESHOLD
    df['prc_up'] = df['prc_change'] > 0
    df['prc_down'] = df['prc_change'] < 0
    
    # Scenario labels
    conditions = [
        df['oi_spike_up'] & df['prc_up'],
        df['oi_spike_up'] & df['prc_down'],
        df['oi_spike_down'] & df['prc_down'],
        df['oi_spike_down'] & df['prc_up'],
    ]
    labels = ['new_longs', 'new_shorts', 'short_squeeze', 'long_liquidation']
    df['scenario'] = np.select(conditions, labels, default='none')
    
    return df

def compute_forward_returns(df):
    """Add forward returns over N bars."""
    df = df.copy()
    for n in FORWARD_BARS:
        df[f'fwd_return_{n}'] = df['prc'].shift(-n) / df['prc'] - 1.0
    return df

def analyze_ticker(ticker):
    """Full analysis pipeline for one ticker."""
    print(f"\n{'='*70}")
    print(f"  TICKER: {ticker}")
    print(f"{'='*70}")
    
    df = query_5m_bars(ticker)
    if df is None or len(df) < ROLLING_WINDOW + 20:
        print(f"  ❌ Not enough data for {ticker}")
        return None
    
    print(f"  Bars: {len(df)}")
    print(f"  Date range: {df['bt'].min()} → {df['bt'].max()}")
    
    # Compute raw OI change first (for outlier detection)
    df['oi_change'] = df['oi'] / df['oi'].shift(1)
    
    # Filter extreme OI outliers (>10x change in 5 min is data noise)
    df = df[df['oi_change'] < 10.0].copy()
    df = df[df['oi_change'] > 0.1].copy()
    
    df = compute_oi_spikes(df)
    df = compute_forward_returns(df)
    
    # Remove rows with NaN z-scores (insufficient lookback)
    df = df.dropna(subset=['z_oi']).copy()
    
    # Count events by scenario
    scenarios = ['new_longs', 'new_shorts', 'short_squeeze', 'long_liquidation']
    event_counts = {s: (df['scenario'] == s).sum() for s in scenarios}
    total_events = sum(event_counts.values())
    
    print(f"  Total OI spike events (|z|>{Z_THRESHOLD}): {total_events}")
    for s in scenarios:
        print(f"    {s}: {event_counts[s]}")
    
    # Results
    results_rows = []
    
    for scenario in scenarios:
        mask = df['scenario'] == scenario
        count = mask.sum()
        
        if count < MIN_EVENTS:
            continue
        
        scenario_data = df[mask].copy()
        
        for n in FORWARD_BARS:
            fwd_col = f'fwd_return_{n}'
            valid = scenario_data[fwd_col].notna()
            n_valid = valid.sum()
            
            if n_valid < MIN_EVENTS:
                continue
            
            fwd_returns = scenario_data.loc[valid, fwd_col]
            mean_ret = fwd_returns.mean()
            median_ret = fwd_returns.median()
            std_ret = fwd_returns.std()
            win_rate = (fwd_returns > 0).mean() * 100
            
            # T-test against zero
            if len(fwd_returns) >= 3 and std_ret > 0:
                t_stat, p_value = stats.ttest_1samp(fwd_returns, 0)
            else:
                t_stat, p_value = 0, 1.0
            
            results_rows.append({
                'ticker': ticker,
                'scenario': scenario,
                'forward_bars': n,
                'events': int(n_valid),
                'win_rate_pct': round(win_rate, 2),
                'mean_return_pct': round(mean_ret * 100, 4),
                'median_return_pct': round(median_ret * 100, 4),
                'std_return_pct': round(std_ret * 100, 4),
                't_stat': round(t_stat, 4),
                'p_value': round(p_value, 6),
                'significant_95': p_value < 0.05,
            })
    
    return results_rows

def print_results(all_results):
    """Print summary table and save CSV."""
    if not all_results:
        print("\n\n  ❌ No results to display.")
        return
    
    results_df = pd.DataFrame(all_results)
    
    print(f"\n\n{'='*90}")
    print("  RESULTS SUMMARY: Intraday OI Spike Test")
    print(f"{'='*90}\n")
    
    for ticker in results_df['ticker'].unique():
        tdf = results_df[results_df['ticker'] == ticker]
        
        print(f"\n  ┌─ {ticker} ─────────────────────────────────────────────────────────────┐")
        print(f"  │ {'Scenario':<20} {'Fwd':>5} {'Events':>8} {'WR%':>8} {'Mean%':>10} {'Med%':>10} {'p-val':>8} │")
        print(f"  ├{'─'*78}┤")
        
        for _, row in tdf.iterrows():
            sig = ' *' if row['significant_95'] else '  '
            print(f"  │ {row['scenario']:<20} {row['forward_bars']:>5} {row['events']:>8} {row['win_rate_pct']:>7.2f}% {row['mean_return_pct']:>9.4f} {row['median_return_pct']:>9.4f} {row['p_value']:>8.5f}{sig} │")
        
        print(f"  └{'─'*78}┘")
    
    # Overall assessment
    print(f"\n\n  ── OVERALL ASSESSMENT ──")
    
    winners = results_df[results_df['win_rate_pct'] > 52.0]
    sig_winners = winners[winners['significant_95']]
    
    if len(sig_winners) > 0:
        print(f"  ✅ SIGNALS DETECTED ({len(sig_winners)} combinations with WR>52% and p<0.05):")
        for _, row in sig_winners.iterrows():
            print(f"     {row['ticker']} | {row['scenario']:<20} | {row['forward_bars']} bars | WR={row['win_rate_pct']:.1f}% | Mean={row['mean_return_pct']:.4f}% | p={row['p_value']:.5f}")
    elif len(winners) > 0:
        print(f"  ⚠️  MARGINAL: {len(winners)} combinations WR>52% but none significant (p<0.05)")
        for _, row in winners.iterrows():
            print(f"     {row['ticker']} | {row['scenario']:<20} | {row['forward_bars']} bars | WR={row['win_rate_pct']:.1f}% | Mean={row['mean_return_pct']:.4f}% | p={row['p_value']:.5f}")
    else:
        print(f"  ❌ NO SIGNAL: No scenario gives WR > 52%")
    
    output_path = '/home/user/oi_spike_results.csv'
    results_df.to_csv(output_path, index=False)
    print(f"\n  📁 Results saved to: {output_path}")
    
    return results_df

def main():
    print(f"🔥 Intraday OI Spike Test for MOEX Futures")
    print(f"   Period: {START_DATE} → {END_DATE}")
    print(f"   Tickers: {', '.join(TICKERS)}")
    print(f"   Z-threshold: {Z_THRESHOLD}")
    print(f"   Rolling window: {ROLLING_WINDOW} bars (5m each = 2.5h window)")
    print(f"   Forward bars: {FORWARD_BARS} ({', '.join([f'{n*5}m' for n in FORWARD_BARS])})")
    
    all_results = []
    
    for ticker in TICKERS:
        try:
            results = analyze_ticker(ticker)
            if results:
                all_results.extend(results)
        except Exception as e:
            print(f"\n  ❌ Error analyzing {ticker}: {e}")
            import traceback
            traceback.print_exc()
    
    if all_results:
        print_results(all_results)
    else:
        print("\n\n  ❌ No results generated.")

if __name__ == '__main__':
    main()
