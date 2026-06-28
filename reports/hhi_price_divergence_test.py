#!/usr/bin/env python3
"""
HHI + Price Divergence test on MOEX futures.
Tests if HHI concentration surge combined with price divergence predicts reversals.
"""

import pandas as pd
import numpy as np
from datetime import datetime, date
import json

CLICKHOUSE_HOST = "10.0.0.60"
CLICKHOUSE_PORT = "8123"
DB = "moex"
START_DATE = "2024-10-01"
END_DATE = "2026-06-28"

def ch_query(query):
    """Execute ClickHouse query and return results as list of dicts."""
    import subprocess
    cmd = [
        "clickhouse-client", "-h", CLICKHOUSE_HOST, "--port", CLICKHOUSE_PORT,
        "-d", DB, "-q", query, "--format", "JSONEachRow"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"CH Error: {result.stderr}")
            return []
        lines = [l for l in result.stdout.strip().split('\n') if l.strip()]
        return [json.loads(l) for l in lines]
    except Exception as e:
        print(f"Query failed: {e}")
        return []

def zscore(series):
    """Compute z-score using expanding mean/std."""
    mean = series.rolling(20).mean()
    std = series.rolling(20).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)

def main():
    print("=== HHI + Price Divergence Test ===")
    print(f"Period: {START_DATE} to {END_DATE}")
    
    # Step 1: Get all asset_codes that have been active
    print("\n[1] Getting asset codes from hi2_fo...")
    assets_raw = ch_query(f"""
        SELECT DISTINCT asset_code 
        FROM moex.hi2_fo 
        WHERE tradedate >= '{START_DATE}' AND tradedate <= '{END_DATE}'
        ORDER BY asset_code
    """)
    asset_codes = [a['asset_code'] for a in assets_raw]
    print(f"Found {len(asset_codes)} asset codes")
    
    # Step 2: For each asset, get best secid per date, HHI, and price
    print("\n[2] Fetching HHI + price data for all assets...")
    
    # We'll use hhi_agressive as the main concentration metric
    # Let me also check hhi_volume for comparison
    metrics_to_test = ['hhi_agressive', 'hhi_volume']
    
    # Fetch all data in one go - join best_secid, hi2_fo, futures_history
    # But ClickHouse might handle this better with smaller batches
    all_results = {}
    
    for metric in metrics_to_test:
        print(f"\n--- Testing metric: {metric} ---")
    
    # Let's first try a batch approach - get all data at once for all assets
    # SQL with window functions for z-scores and forward returns
    print("Fetching combined dataset...")
    
    # Build query that:
    # 1. Gets best secid per day per ticker
    # 2. Joins HHI data
    # 3. Joins prices
    # 4. Computes z-scores and signals
    # 5. Computes forward returns
    
    # Actually let's do it asset by asset for clarity and to avoid massive queries
    results_rows = []
    
    # Filter to a manageable set of liquid futures
    major_assets = [
        'Si', 'SBRF', 'BR', 'GAZR', 'LKOH', 'SBERF', 'ROSN', 'MGNT',
        'GMKN', 'NLMK', 'CHMF', 'ALRS', 'MOEX', 'TATN', 'VTBR',
        'PLZL', 'POLY', 'RUAL', 'MAGN', 'AFLT', 'AFKS', 'FEES',
        'HYDR', 'IRAO', 'MTLR', 'NOTK', 'PIKK', 'RASP', 'RTKM',
        'SGZH', 'SNGP', 'T', 'TCSI', 'YNDF', 'VKCO', 'OZON',
        'FIVE', 'BANE', 'GL', 'NG', 'Si', 'BR', 'GOLD',
        'CNYRUBF', 'EURRUBF', 'USDRUBF', 'IMOEXF', 'RTS'
    ]
    
    # Only test assets that are actually in hi2_fo
    test_assets = [a for a in major_assets if a in asset_codes]
    print(f"Testing {len(test_assets)} major assets: {', '.join(test_assets[:10])}...")
    
    metric_name = 'hhi_agressive'  # Primary metric
    
    for idx, asset_code in enumerate(test_assets):
        if (idx + 1) % 10 == 0:
            print(f"  Processing asset {idx+1}/{len(test_assets)}...")
        
        # Get best secid per date for this asset
        best_secids = ch_query(f"""
            SELECT tradedate, best_secid
            FROM moex._daily_best_secid
            WHERE ticker = '{asset_code}'
              AND tradedate >= '{START_DATE}' AND tradedate <= '{END_DATE}'
            ORDER BY tradedate
        """)
        
        if not best_secids:
            continue
        
        # Build date range
        dates = [b['tradedate'] for b in best_secids]
        secids = [b['best_secid'] for b in best_secids]
        
        # Get HHI data for matching secids and dates
        # We need to join by (secid, tradedate)
        hhi_data = {}
        for b in best_secids:
            tradedate = b['tradedate']
            best_secid = b['best_secid']
            if best_secid is None:
                continue
            
            rows = ch_query(f"""
                SELECT value 
                FROM moex.hi2_fo
                WHERE secid = '{best_secid}'
                  AND tradedate = '{tradedate}'
                  AND metric = '{metric_name}'
                LIMIT 1
            """)
            if rows:
                hhi_data[f"{tradedate}_{best_secid}"] = rows[0]['value']
        
        if not hhi_data:
            continue
        
        # Get price data for all secids we need
        # Fetch all futures_history for this asset's contracts
        uniq_secids = list(set(s for s in secids if s is not None))
        secid_list = "', '".join(uniq_secids)
        
        price_rows = ch_query(f"""
            SELECT date, secid, close, open, high, low, volume
            FROM moex.futures_history
            WHERE secid IN ('{secid_list}')
              AND date >= '{START_DATE}' AND date <= '{END_DATE}'
            ORDER BY date, secid
        """)
        
        # Build price lookup by (date, secid)
        price_lookup = {}
        for pr in price_rows:
            key = f"{pr['date']}_{pr['secid']}"
            price_lookup[key] = {
                'close': pr['close'],
                'open': pr['open'],
                'high': pr['high'],
                'low': pr['low'],
                'volume': pr['volume']
            }
        
        # Now join everything chronologically
        data_rows = []
        for b in best_secids:
            tradedate = b['tradedate']
            best_secid = b['best_secid']
            if best_secid is None:
                continue
            
            hhi_key = f"{tradedate}_{best_secid}"
            price_key = f"{tradedate}_{best_secid}"
            
            hhi_val = hhi_data.get(hhi_key)
            price_val = price_lookup.get(price_key)
            
            if hhi_val is None or price_val is None:
                continue
            
            data_rows.append({
                'date': tradedate,
                'secid': best_secid,
                'asset_code': asset_code,
                'hhi': hhi_val,
                'close': price_val['close'],
                'open': price_val['open'],
                'high': price_val['high'],
                'low': price_val['low'],
                'volume': price_val['volume']
            })
        
        if len(data_rows) < 25:  # Need at least 20 + some forward bars
            continue
        
        df = pd.DataFrame(data_rows)
        df = df.sort_values('date').reset_index(drop=True)
        
        # Compute z-scores
        df['z_hhi'] = zscore(df['hhi'])
        df['z_price'] = zscore(df['close'])
        
        # Forward returns
        df['fwd_ret_3'] = df['close'].shift(-3) / df['close'] - 1
        df['fwd_ret_6'] = df['close'].shift(-6) / df['close'] - 1
        df['fwd_ret_12'] = df['close'].shift(-12) / df['close'] - 1
        
        # Signals
        df['signal_long'] = (df['z_hhi'] > 1.5) & (df['z_price'] < -1.0)
        df['signal_short'] = (df['z_hhi'] > 1.5) & (df['z_price'] > 1.0)
        df['signal'] = 0
        df.loc[df['signal_long'], 'signal'] = 1
        df.loc[df['signal_short'], 'signal'] = -1
        
        # Store results
        signals = df[df['signal'] != 0].copy()
        if len(signals) == 0:
            continue
        
        for period, col in [('3', 'fwd_ret_3'), ('6', 'fwd_ret_6'), ('12', 'fwd_ret_12')]:
            sig_df = signals.dropna(subset=[col])
            if len(sig_df) == 0:
                continue
            
            long_signals = sig_df[sig_df['signal'] == 1]
            short_signals = sig_df[sig_df['signal'] == -1]
            
            for side_name, side_df in [('LONG', long_signals), ('SHORT', short_signals)]:
                if len(side_df) == 0:
                    continue
                
                returns = side_df[col].values
                wr = np.mean(returns > 0) * 100
                mean_ret = np.mean(returns) * 100
                net80 = np.percentile(returns, 80) * 100
                
                results_rows.append({
                    'asset': asset_code,
                    'metric': metric_name,
                    'side': side_name,
                    'period': period,
                    'n_signals': len(returns),
                    'wr_pct': round(wr, 2),
                    'mean_ret_pct': round(mean_ret, 4),
                    'net80_pct': round(net80, 4)
                })
    
    # Build summary table
    if not results_rows:
        print("\nNo signals found!")
        return
    
    results_df = pd.DataFrame(results_rows)
    
    # Filter WR >= 52%
    results_df = results_df[results_df['wr_pct'] >= 52]
    
    # Sort by WR descending
    results_df = results_df.sort_values(['asset', 'side', 'period'])
    
    print("\n\n=== RESULTS SUMMARY ===")
    print(f"Total signal rows (WR>=52%): {len(results_df)}")
    print(f"Unique assets with signals: {results_df['asset'].nunique()}")
    
    # Pivot for better readability
    print("\n\nPer asset summary (best period):")
    
    # Group by asset and get best results
    best_per_asset = results_df.loc[results_df.groupby(['asset', 'side'])['wr_pct'].idxmax()]
    best_per_asset = best_per_asset.sort_values('wr_pct', ascending=False)
    
    print(f"\n{'Asset':<10} {'Side':<6} {'Period':<6} {'N':<5} {'WR%':<7} {'Mean%':<9} {'Net80%':<9}")
    print("-" * 55)
    for _, row in best_per_asset.iterrows():
        print(f"{row['asset']:<10} {row['side']:<6} {row['period']:<6} {row['n_signals']:<5} "
              f"{row['wr_pct']:<7} {row['mean_ret_pct']:<9} {row['net80_pct']:<9}")
    
    # Also check the original metric: which metric worked better
    print("\n\n=== BY METRIC (hhi_agressive) ===")
    
    # Summary statistics
    print(f"\nTotal tests: {len(results_rows)}")
    print(f"Tests with WR>=52%: {len(results_df)}")
    
    overall_wr = results_df['wr_pct'].mean()
    print(f"Average WR (filtered): {overall_wr:.2f}%")
    
    # Save to file
    results_df.to_csv('/home/user/hhi_price_divergence_results.csv', index=False)
    print("\n\nResults saved to /home/user/hhi_price_divergence_results.csv")
    
    # Detailed per-asset breakout
    print("\n\n=== DETAILED PER-ASSET TABLE ===")
    print(results_df.to_string(index=False))

if __name__ == '__main__':
    main()
