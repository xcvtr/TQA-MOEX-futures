#!/usr/bin/env python3
"""
FIZ/YUR Divergence Test v2 — with magnitude thresholds

Refinements:
1. Only count divergence when |dfiz| and |dyur| exceed their respective thresholds
   (threshold = percentile-based, e.g. top 30% of absolute changes)
2. Also test: divergence persistence (2+ bars in a row)
"""

import pandas as pd
import numpy as np
import requests
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

CLICKHOUSE_HOST = 'http://10.0.0.60:8123'
DATABASE = 'moex'
TICKERS = ['Si', 'GZ', 'BR', 'NG', 'CR', 'SR']
START_DATE = '2024-10-01'
END_DATE = datetime.now().strftime('%Y-%m-%d')

def query_ch(query):
    url = f"{CLICKHOUSE_HOST}/?database={DATABASE}"
    r = requests.post(url, data=query, timeout=60)
    r.raise_for_status()
    return r.text

def load_data(ticker):
    print(f"  Loading {ticker}...")
    
    oi_query = f"""
    SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
    FROM moex.prices_5m_oi
    WHERE symbol = '{ticker}' AND time >= '{START_DATE}'
    ORDER BY time
    FORMAT TabSeparatedWithNames
    """
    oi_raw = query_ch(oi_query)
    
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
            data_rows.append({
                'time': parts[0],
                'fiz_buy': int(parts[1]),
                'fiz_sell': int(parts[2]),
                'yur_buy': int(parts[3]),
                'yur_sell': int(parts[4]),
                'total_oi': int(parts[5]),
            })
    
    df = pd.DataFrame(data_rows)
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    
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
            price_rows.append({'time': parts[0], 'close': float(parts[1])})
    
    price_df = pd.DataFrame(price_rows)
    price_df['time'] = pd.to_datetime(price_df['time'])
    price_df.set_index('time', inplace=True)
    price_df = price_df[~price_df.index.duplicated(keep='first')]
    
    merged = df.join(price_df, how='inner')
    print(f"    Rows: {len(merged)}, date range: {merged.index.min()} to {merged.index.max()}")
    return merged

def run_test_v2(ticker):
    """Run refined FIZ/YUR divergence test."""
    df = load_data(ticker)
    if df is None or len(df) < 100:
        return None
    
    # Calculate normalized net flows
    df['fiz_net'] = (df['fiz_buy'] - df['fiz_sell']) / df['total_oi']
    df['yur_net'] = (df['yur_buy'] - df['yur_sell']) / df['total_oi']
    
    # Rate of change
    df['dfiz'] = df['fiz_net'].diff()
    df['dyur'] = df['yur_net'].diff()
    df = df.dropna(subset=['dfiz', 'dyur'])
    
    # Thresholds: use percentile cutoffs for dfiz and dyur magnitude
    fiz_threshold_pct = 60  # top 40% of |dfiz|
    yur_threshold_pct = 60
    
    fiz_thresh = df['dfiz'].abs().quantile(fiz_threshold_pct / 100)
    yur_thresh = df['dyur'].abs().quantile(yur_threshold_pct / 100)
    
    print(f"    |dfiz| threshold (p{fiz_threshold_pct}): {fiz_thresh:.8f}")
    print(f"    |dyur| threshold (p{yur_threshold_pct}): {yur_thresh:.8f}")
    
    # Method 1: Divergence with magnitude threshold
    df['div_mag'] = (df['dfiz'] * df['dyur'] < 0) & \
                    (df['dfiz'].abs() >= fiz_thresh) & \
                    (df['dyur'].abs() >= yur_thresh)
    
    # Method 2: Persistent divergence (2+ consecutive bars of same divergence)
    df['div_raw'] = (df['dfiz'] * df['dyur'] < 0)
    df['div_persist'] = df['div_raw'] & df['div_raw'].shift(1) & df['div_raw'].shift(2)
    
    # Method 3: Extreme divergence - both flows have strong opposite moves
    df['dfiz_std'] = (df['dfiz'] - df['dfiz'].mean()) / df['dfiz'].std()
    df['dyur_std'] = (df['dyur'] - df['dyur'].mean()) / df['dyur'].std()
    df['div_extreme'] = (df['dfiz'] * df['dyur'] < 0) & \
                        (df['dfiz_std'].abs() >= 1.0) & \
                        (df['dyur_std'].abs() >= 1.0)
    
    # Trade direction: follow yur
    df['trade_dir'] = np.sign(df['dyur'])
    
    results = {}
    methods = {
        'mag_threshold': 'div_mag',
        'persistent_3bar': 'div_persist',
        'extreme_1std': 'div_extreme',
    }
    
    for method_name, col in methods.items():
        results[method_name] = {}
        for fwd in [3, 6, 12]:
            df[f'fwd_ret_{fwd}'] = df['close'].pct_change(fwd).shift(-fwd)
            
            signals = df[df[col]].copy()
            if len(signals) < 5:
                results[method_name][fwd] = {
                    'count': len(signals), 'wins': 0, 'win_rate': 0, 'avg_ret': 0
                }
                continue
            
            signals['strat_ret'] = signals['trade_dir'] * signals[f'fwd_ret_{fwd}']
            
            wins = (signals['strat_ret'] > 0).sum()
            total = len(signals)
            win_rate = wins / total * 100
            avg_ret = signals['strat_ret'].mean() * 100
            avg_fwd_ret = signals[f'fwd_ret_{fwd}'].mean() * 100
            
            results[method_name][fwd] = {
                'count': total,
                'wins': wins,
                'win_rate': win_rate,
                'avg_ret': avg_ret,
                'avg_fwd_ret': avg_fwd_ret,
            }
    
    return results, fiz_thresh, yur_thresh

def main():
    print(f"FIZ/YUR Divergence Test v2 (5m data) — with magnitude thresholds")
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"{'='*100}")
    
    all_results = {}
    
    for ticker in TICKERS:
        print(f"\n--- {ticker} ---")
        res = run_test_v2(ticker)
        if res is None:
            print(f"  NO DATA")
            continue
        
        results, fiz_th, yur_th = res
        all_results[ticker] = results
        
        for method_name in ['mag_threshold', 'persistent_3bar', 'extreme_1std']:
            print(f"  [{method_name}]")
            for fwd in [3, 6, 12]:
                r = results[method_name][fwd]
                wr_str = f"{r['win_rate']:.1f}%" if r['count'] > 0 else "N/A"
                signal = "✅" if (r['count'] > 0 and r['win_rate'] >= 52) else "❌"
                print(f"    FWD {fwd:2d} | Sig: {r['count']:5d} | Wins: {r['wins']:5d} | WR: {wr_str:>6s} | AvgRet: {r['avg_ret']:+.4f}% {signal}")
    
    # Summary table
    print(f"\n{'='*120}")
    print(f"{'SUMMARY':^120}")
    print(f"{'='*120}")
    
    for method_name in ['mag_threshold', 'persistent_3bar', 'extreme_1std']:
        print(f"\n--- Method: {method_name} ---")
        print(f"{'Ticker':<8} {'Fwd':>4} {'Signals':>8} {'Wins':>6} {'WR%':>7} {'AvgRet%':>10} {'Signal':>8}")
        print(f"{'-'*55}")
        for ticker in TICKERS:
            if ticker not in all_results:
                print(f"{ticker:<8} {'---':>4} {'N/A':>8} {'N/A':>6} {'N/A':>7} {'N/A':>10} {'N/A':>8}")
                continue
            for fwd in [3, 6, 12]:
                r = all_results[ticker][method_name][fwd]
                wr = r['win_rate']
                signal = "YES ✅" if (r['count'] > 0 and wr >= 52) else "NO ❌"
                wr_str = f"{wr:.1f}" if r['count'] > 0 else "N/A"
                avg_str = f"{r['avg_ret']:+.4f}" if r['count'] > 0 else "N/A"
                print(f"{ticker:<8} {fwd:>4} {r['count']:>8} {r['wins']:>6} {wr_str:>7} {avg_str:>10} {signal:>8}")

if __name__ == '__main__':
    main()
