#!/usr/bin/env python3
"""
HHI + Price Divergence test on MOEX futures - v3 (HTTP).
Tests if HHI concentration surge combined with price divergence predicts reversals.
"""

import pandas as pd
import numpy as np
from datetime import datetime, date
import json
import urllib.request
import urllib.error
from collections import defaultdict
import sys

CLICKHOUSE_HOST = "10.0.0.60"
CLICKHOUSE_PORT = "8123"
DB = "moex"
START_DATE = "2024-10-01"
END_DATE = "2026-06-28"

def ch_query(query):
    """Execute ClickHouse query via HTTP and return results as list of dicts."""
    url = f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}/?database={DB}"
    data = query.encode('utf-8')
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode('utf-8')
            lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
            return [json.loads(l) for l in lines]
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode()[:500]}")
        return []
    except Exception as e:
        print(f"Query error: {e}")
        return []

def zscore(series):
    """Compute z-score over rolling 20 bars."""
    mean = series.rolling(20, min_periods=20).mean()
    std = series.rolling(20, min_periods=20).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)

def process_group(df):
    """Process a single asset's data and return signal results."""
    if len(df) < 25:
        return []
    
    df = df.sort_values('date').reset_index(drop=True)
    
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
    
    signals = df[df['signal'] != 0].copy()
    if len(signals) == 0:
        return []
    
    results = []
    for period, col in [('3', 'fwd_ret_3'), ('6', 'fwd_ret_6'), ('12', 'fwd_ret_12')]:
        sig_df = signals.dropna(subset=[col])
        if len(sig_df) == 0:
            continue
        
        long_sigs = sig_df[sig_df['signal'] == 1]
        short_sigs = sig_df[sig_df['signal'] == -1]
        
        for side_name, side_df in [('LONG', long_sigs), ('SHORT', short_sigs)]:
            if len(side_df) < 3:
                continue
            returns = side_df[col].values
            wr = float(np.mean(returns > 0) * 100)
            mean_ret = float(np.mean(returns) * 100)
            net80 = float(np.percentile(returns, 80) * 100)
            results.append({
                'asset': df['asset_code'].iloc[0],
                'side': side_name,
                'period': period,
                'n_signals': len(returns),
                'wr_pct': round(wr, 2),
                'mean_ret_pct': round(mean_ret, 4),
                'net80_pct': round(net80, 4)
            })
    return results

def main():
    print("=== HHI + Price Divergence Test v3 (HTTP) ===")
    print(f"Period: {START_DATE} to {END_DATE}")
    
    # Step 1: Get asset_code -> ticker mapping
    print("\n[1] Getting asset_code to ticker mapping...")
    mapping_raw = ch_query("""
        SELECT DISTINCT asset_code, substring(secid, 1, 2) as ticker
        FROM moex.hi2_fo
        WHERE tradedate >= '2024-10-01'
        GROUP BY asset_code, ticker
        ORDER BY asset_code
    """)
    
    if not mapping_raw:
        print("Failed to get mappings!")
        return
    
    asset_to_ticker = {m['asset_code']: m['ticker'] for m in mapping_raw}
    ticker_to_asset = {m['ticker']: m['asset_code'] for m in mapping_raw}
    
    print(f"Found {len(asset_to_ticker)} asset-to-ticker mappings")
    
    # Define major liquid assets to test
    major_assets = [
        'Si', 'SBRF', 'BR', 'GAZR', 'LKOH', 'SBERF', 'ROSN', 'MGNT',
        'GMKN', 'NLMK', 'CHMF', 'ALRS', 'MOEX', 'TATN', 'VTBR',
        'PLZL', 'POLY', 'RUAL', 'MAGN', 'AFLT', 'AFKS', 'FEES',
        'HYDR', 'IRAO', 'MTLR', 'NOTK', 'PIKK', 'RASP', 'RTKM',
        'SGZH', 'SNGP', 'T', 'TCSI', 'YNDF', 'VKCO', 'OZON',
        'FIVE', 'BANE', 'GL', 'NG', 'GOLD', 'EURRUBF',
        'CNYRUBF', 'USDRUBF', 'IMOEXF', 'RTS', 'ASTR', 'BSPB',
        'CBOM', 'ENPG', 'FLOT', 'HEAD', 'MTSI', 'MVID',
        'PHOR', 'POSI', 'SFIN', 'SOFL', 'SPBE', 'SVCB',
        'TRNF', 'X5', 'YDEX', 'BELUGA', 'ALIBABA'
    ]
    
    test_assets = [a for a in major_assets if a in asset_to_ticker]
    print(f"Testing {len(test_assets)} assets: {', '.join(test_assets[:20])}...")
    
    test_tickers = [asset_to_ticker[a] for a in test_assets]
    ticker_list = "','".join(test_tickers)
    
    # Step 2: Get best_secid data for all tickers at once
    print("\n[2] Fetching best_secid data...")
    best_data = ch_query(f"""
        SELECT bs.tradedate, bs.ticker, bs.best_secid
        FROM moex._daily_best_secid bs
        WHERE bs.ticker IN ('{ticker_list}')
          AND bs.tradedate >= '{START_DATE}' AND bs.tradedate <= '{END_DATE}'
          AND bs.best_secid IS NOT NULL
        ORDER BY bs.ticker, bs.tradedate
    """)
    print(f"Got {len(best_data)} best_secid records")
    
    if not best_data:
        print("No best_secid data!")
        return
    
    # Group by ticker
    ticker_dates = defaultdict(list)
    for row in best_data:
        ticker_dates[row['ticker']].append(row)
    
    # Step 3: Process each ticker
    print(f"\n[3] Processing {len(ticker_dates)} assets...")
    
    all_results = []
    processed = 0
    total = len(ticker_dates)
    
    for ticker, rows in ticker_dates.items():
        asset_code = ticker_to_asset.get(ticker, ticker)
        
        # Progress
        processed += 1
        if processed % 5 == 0 or processed == 1:
            print(f"  [{processed}/{total}] {asset_code}...")
        
        uniq_secids = list(set(r['best_secid'] for r in rows if r['best_secid']))
        if not uniq_secids:
            continue
        
        secid_str = "','".join(uniq_secids)
        
        # Get HHI data
        hhi_rows = ch_query(f"""
            SELECT h.tradedate, h.secid, h.value as hhi
            FROM moex.hi2_fo h
            WHERE h.secid IN ('{secid_str}')
              AND h.tradedate >= '{START_DATE}' AND h.tradedate <= '{END_DATE}'
              AND h.metric = 'hhi_agressive'
            ORDER BY h.tradedate, h.secid
        """)
        
        if not hhi_rows:
            continue
        
        hhi_lookup = {}
        for r in hhi_rows:
            hhi_lookup[(r['tradedate'], r['secid'])] = float(r['hhi'])
        
        # Get price data
        price_rows = ch_query(f"""
            SELECT f.date, f.secid, f.close
            FROM moex.futures_history f
            WHERE f.secid IN ('{secid_str}')
              AND f.date >= '{START_DATE}' AND f.date <= '{END_DATE}'
            ORDER BY f.date, f.secid
        """)
        
        if not price_rows:
            continue
        
        price_lookup = {}
        for r in price_rows:
            price_lookup[(r['date'], r['secid'])] = float(r['close'])
        
        # Build combined dataset
        data_rows = []
        for r in rows:
            tradedate = r['tradedate']
            secid = r['best_secid']
            hhi_val = hhi_lookup.get((tradedate, secid))
            close_val = price_lookup.get((tradedate, secid))
            
            if hhi_val is not None and close_val is not None:
                data_rows.append({
                    'date': tradedate,
                    'secid': secid,
                    'asset_code': asset_code,
                    'hhi': hhi_val,
                    'close': close_val
                })
        
        if len(data_rows) < 25:
            continue
        
        df = pd.DataFrame(data_rows)
        results = process_group(df)
        
        if results:
            all_results.extend(results)
    
    # Step 4: Results summary
    print(f"\n\nProcessed {processed} assets total")
    
    if not all_results:
        print("\nNo signals found for any asset!")
        return
    
    rdf = pd.DataFrame(all_results)
    
    # Filter WR >= 52%
    rdf_f = rdf[rdf['wr_pct'] >= 52].copy()
    
    print(f"\nTotal tests: {len(rdf)}")
    print(f"Tests with WR >= 52%: {len(rdf_f)}")
    print(f"Unique assets with signals: {rdf['asset'].nunique()}")
    print(f"Unique assets with WR>=52%: {rdf_f['asset'].nunique()}")
    
    if rdf_f.empty:
        print("\nNo signals with WR >= 52%. Showing best signals anyway:")
        best_all = rdf.loc[rdf.groupby(['asset', 'side'])['wr_pct'].idxmax()]
        best_all = best_all.sort_values('wr_pct', ascending=False).head(20)
        print(f"\n{'Asset':<12} {'Side':<6} {'Period':<6} {'N':<5} {'WR%':<7} {'Mean%':<9} {'Net80%':<9}")
        print("-" * 57)
        for _, row in best_all.iterrows():
            print(f"{row['asset']:<12} {row['side']:<6} {row['period']:<6} {row['n_signals']:<5} "
                  f"{row['wr_pct']:<7} {row['mean_ret_pct']:<9} {row['net80_pct']:<9}")
    
    else:
        # Summary per asset-side, best period
        print("\n\n=== Best signal per asset-side (WR >= 52%) ===")
        best = rdf_f.loc[rdf_f.groupby(['asset', 'side'])['wr_pct'].idxmax()]
        best = best.sort_values('wr_pct', ascending=False)
        
        print(f"\n{'Asset':<12} {'Side':<6} {'Period':<6} {'N':<5} {'WR%':<7} {'Mean%':<9} {'Net80%':<9}")
        print("-" * 57)
        for _, row in best.iterrows():
            print(f"{row['asset']:<12} {row['side']:<6} {row['period']:<6} {row['n_signals']:<5} "
                  f"{row['wr_pct']:<7} {row['mean_ret_pct']:<9} {row['net80_pct']:<9}")
    
    # Full detail
    print(f"\n\n=== Full detail ({len(rdf)} signal tests total) ===")
    rdf_sorted = rdf.sort_values(['asset', 'side', 'period'])
    print(rdf_sorted.to_string(index=False))
    
    # Save
    rdf.to_csv('/home/user/hhi_price_divergence_all.csv', index=False)
    rdf_f.to_csv('/home/user/hhi_price_divergence_filtered.csv', index=False)
    print("\n\nFiles saved:")
    print("  /home/user/hhi_price_divergence_all.csv")
    print("  /home/user/hhi_price_divergence_filtered.csv")

if __name__ == '__main__':
    main()
