#!/usr/bin/env python3
"""
FIZ/YUR Divergence Test on MOEX Futures 5m Data

Strategy:
1. fiz_net = (fiz_buy - fiz_sell) / total_oi
2. yur_net = (yur_buy - yur_sell) / total_oi
3. dfiz = diff(fiz_net), dyur = diff(yur_net)
4. Signal when dfiz and dyur have OPPOSITE signs (divergence)
5. Trade in direction of yur (institutions are usually right)
6. Measure forward returns at 3, 6, 12 bars
"""

import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

CLICKHOUSE_HOST = 'http://10.0.0.60:8123'
DATABASE = 'moex'
TICKERS = ['Si', 'GZ', 'BR', 'NG', 'CR', 'SR']
START_DATE = '2024-10-01'
END_DATE = datetime.now().strftime('%Y-%m-%d')

def query_ch(query):
    """Execute ClickHouse query via HTTP and return tab-separated results."""
    url = f"{CLICKHOUSE_HOST}/?database={DATABASE}"
    r = requests.post(url, data=query, timeout=60)
    r.raise_for_status()
    return r.text

def load_data(ticker):
    """Load and merge OI data with price data for a ticker."""
    print(f"  Loading {ticker}...")
    
    # Get OI data
    oi_query = f"""
    SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
    FROM moex.prices_5m_oi
    WHERE symbol = '{ticker}' AND time >= '{START_DATE}'
    ORDER BY time
    FORMAT TabSeparatedWithNames
    """
    oi_raw = query_ch(oi_query)
    
    # Parse OI data
    lines = oi_raw.strip().split('\n')
    if len(lines) <= 1:
        print(f"  No OI data for {ticker}")
        return None
    
    col_names = lines[0].split('\t')
    data_rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 6:
            row = {
                'time': parts[0],
                'fiz_buy': int(parts[1]),
                'fiz_sell': int(parts[2]),
                'yur_buy': int(parts[3]),
                'yur_sell': int(parts[4]),
                'total_oi': int(parts[5]),
            }
            data_rows.append(row)
    
    df = pd.DataFrame(data_rows)
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    
    # Get close prices
    price_query = f"""
    SELECT time, close
    FROM moex.prices_5m
    WHERE symbol = '{ticker}' AND time >= '{START_DATE}'
    ORDER BY time
    FORMAT TabSeparatedWithNames
    """
    price_raw = query_ch(price_query)
    
    lines = price_raw.strip().split('\n')
    if len(lines) <= 1:
        print(f"  No price data for {ticker}")
        return None
    
    price_rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 2 and parts[1] != '\\N' and parts[1] != '':
            price_rows.append({
                'time': parts[0],
                'close': float(parts[1]),
            })
    
    price_df = pd.DataFrame(price_rows)
    price_df['time'] = pd.to_datetime(price_df['time'])
    price_df.set_index('time', inplace=True)
    price_df = price_df[~price_df.index.duplicated(keep='first')]
    
    # Merge OI data with prices
    merged = df.join(price_df, how='inner')
    
    print(f"    Rows: {len(merged)}, date range: {merged.index.min()} to {merged.index.max()}")
    
    return merged

def run_test(ticker):
    """Run FIZ/YUR divergence test for a ticker."""
    df = load_data(ticker)
    if df is None or len(df) < 50:
        return None
    
    # Calculate normalized net flows
    df['fiz_net'] = (df['fiz_buy'] - df['fiz_sell']) / df['total_oi']
    df['yur_net'] = (df['yur_buy'] - df['yur_sell']) / df['total_oi']
    
    # Rate of change (1st difference)
    df['dfiz'] = df['fiz_net'].diff()
    df['dyur'] = df['yur_net'].diff()
    
    # Remove first row with NaN diffs
    df = df.dropna(subset=['dfiz', 'dyur'])
    
    # Signal: dfiz and dyur have opposite signs
    df['divergence'] = (df['dfiz'] * df['dyur']) < 0
    
    # Trade direction: follow yur (institutions)
    # If dyur > 0 (yur net buying), we go long. If dyur < 0, we go short.
    df['trade_dir'] = np.sign(df['dyur'])
    
    # Forward returns at 3, 6, 12 bars
    results = {}
    for fwd in [3, 6, 12]:
        df[f'fwd_ret_{fwd}'] = df['close'].pct_change(fwd).shift(-fwd)
        
        # Filter divergence signals only
        div_signals = df[df['divergence']].copy()
        if len(div_signals) == 0:
            results[fwd] = {'count': 0, 'win_rate': 0, 'avg_ret': 0}
            continue
        
        # Strategy return = trade_dir * forward_return
        div_signals['strat_ret'] = div_signals['trade_dir'] * div_signals[f'fwd_ret_{fwd}']
        
        wins = (div_signals['strat_ret'] > 0).sum()
        total = len(div_signals)
        win_rate = wins / total * 100 if total > 0 else 0
        avg_ret = div_signals['strat_ret'].mean() * 100  # in percent
        avg_fwd = div_signals[f'fwd_ret_{fwd}'].mean() * 100
        
        results[fwd] = {
            'count': total,
            'wins': wins,
            'win_rate': win_rate,
            'avg_ret': avg_ret,
            'avg_fwd_ret': avg_fwd,
        }
    
    return results

def main():
    print(f"FIZ/YUR Divergence Test (5m data)")
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"{'='*80}")
    
    all_results = {}
    for ticker in TICKERS:
        print(f"\n--- {ticker} ---")
        res = run_test(ticker)
        if res is None:
            print(f"  NO DATA or too few rows")
            continue
        all_results[ticker] = res
        
        for fwd in [3, 6, 12]:
            r = res[fwd]
            wr_str = f"{r['win_rate']:.1f}%" if r['count'] > 0 else "N/A"
            signal_str = " ✅ SIGNAL" if (r['count'] > 0 and r['win_rate'] >= 52) else " ❌ NO SIGNAL"
            print(f"  FWD {fwd:2d} bars | Signals: {r['count']:5d} | Wins: {r['wins']:5d} | WR: {wr_str:>6s} | AvgRet: {r['avg_ret']:+.4f}%{signal_str}")
    
    # Summary table
    print(f"\n{'='*80}")
    print(f"{'SUMMARY TABLE':^80}")
    print(f"{'='*80}")
    print(f"{'Ticker':<8} {'Fwd':>4} {'Signals':>8} {'Wins':>6} {'WR%':>7} {'AvgRet%':>10} {'Signal':>8}")
    print(f"{'-'*80}")
    
    for ticker in TICKERS:
        if ticker not in all_results:
            print(f"{ticker:<8} {'---':>4} {'N/A':>8} {'N/A':>6} {'N/A':>7} {'N/A':>10} {'N/A':>8}")
            continue
        for fwd in [3, 6, 12]:
            r = all_results[ticker][fwd]
            wr = r['win_rate']
            signal = "YES ✅" if (r['count'] > 0 and wr >= 52) else "NO ❌"
            wr_str = f"{wr:.1f}" if r['count'] > 0 else "N/A"
            avg_str = f"{r['avg_ret']:+.4f}" if r['count'] > 0 else "N/A"
            print(f"{ticker:<8} {fwd:>4} {r['count']:>8} {r['wins']:>6} {wr_str:>7} {avg_str:>10} {signal:>8}")

if __name__ == '__main__':
    main()
