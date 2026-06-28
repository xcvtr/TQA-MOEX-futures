#!/usr/bin/env python3
"""
Cross-ticker Correlation Divergence — MOEX Futures
Pairs: SiU6/CRU6, BRN6/CRU6
Period: last 30 days
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import json

CLICKHOUSE_URL = 'http://10.0.0.60:8123/'

def query_ch(sql):
    """Execute SQL on ClickHouse, return list of dicts."""
    r = requests.post(CLICKHOUSE_URL, data=sql.encode('utf-8'), timeout=60)
    if r.status_code != 200:
        raise Exception(f"ClickHouse error {r.status_code}: {r.text[:500]}")
    if not r.text.strip():
        return []
    rows = []
    lines = r.text.strip().split('\n')
    for line in lines:
        parts = line.split('\t')
        rows.append(parts)
    return rows
    
def query_ch_df(sql, columns):
    """Execute SQL and return DataFrame."""
    rows = query_ch(sql)
    data = {col: [] for col in columns}
    for row in rows:
        for i, col in enumerate(columns):
            val = row[i] if i < len(row) else None
            if val == '' or val == '\\N' or val == 'NULL':
                val = None
            data[col].append(val)
    df = pd.DataFrame(data)
    return df

def get_5m_bars(secid, start_date, end_date):
    """Get 5-minute OHLC data from tradestats_fo."""
    sql = f"""
    SELECT 
        tradedate,
        tradetime,
        pr_open,
        pr_high,
        pr_low,
        pr_close,
        vol,
        val
    FROM moex.tradestats_fo
    WHERE secid = '{secid}'
      AND tradedate >= '{start_date}'
      AND tradedate <= '{end_date}'
      AND pr_close IS NOT NULL
    ORDER BY tradedate, tradetime
    FORMAT TabSeparated
    """
    cols = ['date', 'time', 'open', 'high', 'low', 'close', 'vol', 'val']
    df = query_ch_df(sql, cols)
    if len(df) == 0:
        return df
    
    # Convert types
    for c in ['open', 'high', 'low', 'close', 'vol', 'val']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    
    # Create datetime
    df['datetime'] = pd.to_datetime(df['date'].astype(str) + ' ' + df['time'].astype(str))
    
    # Remove weekends
    df = df[df['datetime'].dt.dayofweek < 5].copy()
    
    # Resample to exact 5-min bars
    df = df.set_index('datetime')
    
    return df

def detect_divergence(df_si, df_cr, pair_name, window=20, lookaheads=[3, 6, 12]):
    """
    Detect correlation divergence events:
    1. Calculate rolling 20-bar return correlation
    2. When corr drops from >0.7 to <0.3 in 12 bars → structural shift
    3. If CR weaker than Si → long CR/Si pair
    4. Forward returns at 3, 6, 12 bars
    """
    # Merge on datetime index
    combined = pd.DataFrame()
    combined['si_close'] = df_si['close']
    combined['cr_close'] = df_cr['close']
    combined = combined.dropna()
    
    if len(combined) < 50:
        print(f"  {pair_name}: insufficient data after merge ({len(combined)} bars)")
        return None
    
    # Returns
    combined['si_ret'] = combined['si_close'].pct_change()
    combined['cr_ret'] = combined['cr_close'].pct_change()
    
    # Rolling correlation (20 bars)
    combined['rolling_corr'] = combined['si_ret'].rolling(window).corr(combined['cr_ret'])
    
    # Find divergence events
    # Corr > 0.7 then drops to < 0.3 within 12 bars
    corr = combined['rolling_corr'].values
    n = len(corr)
    
    events = []
    lookback = 12  # 1 hour = 12 five-min bars
    
    for i in range(window + lookback, n):
        # Check if current corr is low (< 0.3)
        if np.isnan(corr[i]) or corr[i] >= 0.3:
            continue
        
        # Look back 12 bars for a high corr (> 0.7)
        high_found = False
        for j in range(max(0, i - lookback), i):
            if not np.isnan(corr[j]) and corr[j] > 0.7:
                high_found = True
                break
        
        if high_found:
            # Calculate which is weaker
            si_ret_event = combined['si_ret'].iloc[i]
            cr_ret_event = combined['cr_ret'].iloc[i]
            
            # Direction: if CR weaker → long CR/Si pair
            direction = 'LONG_CR' if cr_ret_event < si_ret_event else 'LONG_SI'
            
            events.append({
                'pair': pair_name,
                'bar_time': combined.index[i],
                'corr_before': float(corr[max(0, i - lookback):i].max()) if i > lookback else float(corr[i]),
                'corr_now': float(corr[i]),
                'si_ret_bar': float(si_ret_event) if not np.isnan(si_ret_event) else 0,
                'cr_ret_bar': float(cr_ret_event) if not np.isnan(cr_ret_event) else 0,
                'direction': direction,
                'si_close': float(combined['si_close'].iloc[i]),
                'cr_close': float(combined['cr_close'].iloc[i]),
            })
    
    print(f"  {pair_name}: {len(events)} divergence events detected")
    
    if len(events) == 0:
        return None
    
    # Forward returns
    for ev in events:
        idx = combined.index.get_loc(ev['bar_time'])
        for la in lookaheads:
            fwd_idx = min(idx + la, len(combined) - 1)
            fwd_si = combined['si_close'].iloc[fwd_idx]
            fwd_cr = combined['cr_close'].iloc[fwd_idx]
            cur_si = combined['si_close'].iloc[idx]
            cur_cr = combined['cr_close'].iloc[idx]
            
            si_fwd_ret = (fwd_si - cur_si) / cur_si
            cr_fwd_ret = (fwd_cr - cur_cr) / cur_cr
            
            # If LONG_CR: we want CR to outperform Si
            if ev['direction'] == 'LONG_CR':
                ev[f'fwd_{la}_ret'] = float(cr_fwd_ret - si_fwd_ret)
            else:
                ev[f'fwd_{la}_ret'] = float(si_fwd_ret - cr_fwd_ret)
            
            ev[f'fwd_{la}_win'] = 1 if ev[f'fwd_{la}_ret'] > 0 else 0
    
    # Aggregate results
    results = {
        'pair': pair_name,
        'total_events': len(events),
        'direction_counts': {'LONG_CR': sum(1 for e in events if e['direction'] == 'LONG_CR'),
                             'LONG_SI': sum(1 for e in events if e['direction'] == 'LONG_SI')},
    }
    
    for la in lookaheads:
        fwd_rets = [e[f'fwd_{la}_ret'] for e in events]
        fwd_wins = [e[f'fwd_{la}_win'] for e in events]
        results[f'fwd_{la}_mean_ret'] = float(np.mean(fwd_rets)) * 100  # in %
        results[f'fwd_{la}_mean_ret_bps'] = float(np.mean(fwd_rets)) * 10000  # in bps
        results[f'fwd_{la}_wr'] = float(np.mean(fwd_wins)) * 100  # in %
        results[f'fwd_{la}_std'] = float(np.std(fwd_rets)) * 100
    
    return results, events

def main():
    today = datetime.now().strftime('%Y-%m-%d')
    start_30d = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    # Fall back to 14 days if needed
    start_14d = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
    
    print(f"=== Cross-ticker Correlation Divergence ===")
    print(f"Period: {start_30d} to {today}")
    print(f"Data source: moex.tradestats_fo (ClickHouse)")
    print()
    
    # Define ticker pairs (use most liquid contracts)
    # SiU6 = USDRUB Sep 2026, CRU6 = CNYRUB Sep 2026
    # BRN6 = Brent Jul 2026, CRU6 = CNYRUB Sep 2026
    pairs = [
        ('SiU6', 'CRU6', 'Si/CR'),
        ('BRN6', 'CRU6', 'BR/CR'),
    ]
    
    all_results = []
    
    for ticker_a, ticker_b, pair_name in pairs:
        print(f"\n--- Pair: {pair_name} ({ticker_a} vs {ticker_b}) ---")
        
        try:
            # Try 30 days first
            df_a = get_5m_bars(ticker_a, start_30d, today)
            df_b = get_5m_bars(ticker_b, start_30d, today)
            
            print(f"  {ticker_a}: {len(df_a)} bars, {ticker_b}: {len(df_b)} bars")
            
            if len(df_a) < 100 or len(df_b) < 100:
                print(f"  ⚠ Not enough data with 30d window, trying 14d...")
                df_a = get_5m_bars(ticker_a, start_14d, today)
                df_b = get_5m_bars(ticker_b, start_14d, today)
                print(f"  {ticker_a}: {len(df_a)} bars, {ticker_b}: {len(df_b)} bars (14d)")
            
            result = detect_divergence(df_a, df_b, pair_name)
            
            if result is None:
                print(f"  ❌ No divergence events found or insufficient data")
            else:
                res, events = result
                all_results.append((res, events))
                
                # Print summary
                print(f"  ✅ {res['total_events']} events detected")
                print(f"  Direction: LONG_CR={res['direction_counts']['LONG_CR']}, LONG_SI={res['direction_counts']['LONG_SI']}")
                for la in [3, 6, 12]:
                    wr = res[f'fwd_{la}_wr']
                    mean_ret = res[f'fwd_{la}_mean_ret_bps']
                    signal = "✅ SIGNAL" if wr >= 52 else "❌ NO SIGNAL"
                    print(f"  Forward {la} bars: WR={wr:.1f}%, mean={mean_ret:.1f}bps {signal}")
                
                # Show first 3 events
                print(f"\n  Sample events (first 3):")
                for e in events[:3]:
                    print(f"    {e['bar_time']} | corr: {e['corr_before']:.2f}→{e['corr_now']:.2f} | dir={e['direction']}")
                
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    # Final summary table
    print("\n" + "="*80)
    print("FINAL RESULTS TABLE")
    print("="*80)
    print(f"{'Pair':<15} {'Events':<8} {'Dir(CR/Si)':<12} {'WR3':<8} {'WR6':<8} {'WR12':<8} {'R3(bps)':<10} {'R6(bps)':<10} {'R12(bps)':<10} {'Signal':<10}")
    print("-"*80)
    
    for res, events in all_results:
        wr3 = res['fwd_3_wr']
        wr6 = res['fwd_6_wr']
        wr12 = res['fwd_12_wr']
        r3 = res['fwd_3_mean_ret_bps']
        r6 = res['fwd_6_mean_ret_bps']
        r12 = res['fwd_12_mean_ret_bps']
        
        max_wr = max(wr3, wr6, wr12)
        signal = "✅" if max_wr >= 52 else "❌"
        
        dir_str = f"CR:{res['direction_counts']['LONG_CR']}/Si:{res['direction_counts']['LONG_SI']}"
        
        print(f"{res['pair']:<15} {res['total_events']:<8} {dir_str:<12} {wr3:<8.1f} {wr6:<8.1f} {wr12:<8.1f} {r3:<10.1f} {r6:<10.1f} {r12:<10.1f} {signal:<10}")
    
    print("="*80)
    print("Note: WR < 52% → NO SIGNAL")
    print()

if __name__ == '__main__':
    main()
