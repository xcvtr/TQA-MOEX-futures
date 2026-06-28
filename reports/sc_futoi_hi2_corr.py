#!/usr/bin/env python3
"""Correlation analysis: supercandles features vs FUTOI/HI2 data.
Fix: hi2_fo is EAV format (metric+value), run pivoted query in CH."""

import json
import sys

import numpy as np
import pandas as pd

try:
    import clickhouse_connect
except ImportError:
    print(json.dumps({"error": "clickhouse_connect not installed"}))
    sys.exit(1)

CH_HOSTS = ["10.0.0.60", "10.0.0.63"]

def get_client():
    for host in CH_HOSTS:
        try:
            return clickhouse_connect.get_client(host=host)
        except Exception:
            continue
    raise RuntimeError("No CH host available")

# Ключевые тикеры для анализа
TICKERS = ["Si", "GL", "CR", "BR", "Eu", "GD", "SR", "AF", "RB", "PD", "PT", "RI", "ED", "NG", "CC", "SF", "MM", "NM"]

def load_data(client, ticker):
    """Load daily data from all 3 sources for a ticker, merged."""
    # Supercandles daily — используем tradedate (Date), а не toDate(tradetime)
    sc_q = f"""
    SELECT tradedate as dt,
           argMax(pr_close, tradetime) as close,
           sum(vol_sum) as volume,
           avg(disb_mean) as disb_avg,
           avg(net_vol_pct) as net_vol_pct_avg,
           avg(vol_b_ratio) as vol_b_ratio_avg,
           max(oi_change) as oi_change_max,
           sum(net_vol) as net_vol_sum,
           sum(vol_b_sum) as vol_b_total,
           sum(vol_s_sum) as vol_s_total,
           count() as n_bars_5m
    FROM moex.supercandles_fo
    WHERE ticker = '{ticker}'
      AND tradedate >= '2024-01-01'
    GROUP BY dt
    ORDER BY dt
    """
    
    # FUTOI daily — используем tradedate (Date)
    futoi_q = f"""
    SELECT tradedate as dt,
           argMax(pos, tradetime) as yur_net,
           argMax(pos_long_num, tradetime) as yur_long_acc,
           argMax(pos_short_num, tradetime) as yur_short_acc,
           max(pos) - min(pos) as yur_net_range
    FROM moex.futoi
    WHERE ticker = '{ticker}' AND clgroup = 'YUR'
      AND tradedate >= '2024-01-01'
    GROUP BY tradedate
    ORDER BY dt
    """
    
    # HI2 — тоже через tradedate
    hi2_ticker = ticker
    # GL → GL, Si → Si, CR → CNY (в hi2 CR это CNY), BR → BR, Eu → Eu и т.д.
    hi2_ticker = ticker
    if ticker == 'CR':
        hi2_ticker = 'CNY'
    elif ticker == 'GL':
        hi2_ticker = 'GL'
    # и т.д. — остальные совпадают
    
    hi2_q = f"""
    SELECT toDate(tradetime) as dt,
           argMax(if(metric='hhi_volume', value, NULL), tradetime) as hhi_vol,
           argMax(if(metric='hhi_agressive', value, NULL), tradetime) as hhi_agr,
           argMax(if(metric='hhi_passive', value, NULL), tradetime) as hhi_pas,
           argMax(if(metric='hhi_agressive_buy', value, NULL), tradetime) as hhi_agr_buy,
           argMax(if(metric='hhi_agressive_sell', value, NULL), tradetime) as hhi_agr_sell,
           argMax(if(metric='hhi_netflow_buy', value, NULL), tradetime) as hhi_net_buy,
           argMax(if(metric='hhi_netflow_sell', value, NULL), tradetime) as hhi_net_sell,
           argMax(if(metric='hhi_buy', value, NULL), tradetime) as hhi_buy,
           argMax(if(metric='hhi_sell', value, NULL), tradetime) as hhi_sell
    FROM moex.hi2_fo
    WHERE asset_code = '{hi2_ticker}'
      AND tradedate >= '2024-01-01'
      AND metric IN ('hhi_volume', 'hhi_agressive', 'hhi_passive',
                     'hhi_agressive_buy', 'hhi_agressive_sell',
                     'hhi_netflow_buy', 'hhi_netflow_sell',
                     'hhi_buy', 'hhi_sell')
    GROUP BY dt
    ORDER BY dt
    """
    
    try:
        sc = client.query_df(sc_q)
        futoi = client.query_df(futoi_q)
        hi2 = client.query_df(hi2_q)
    except Exception as e:
        return None, str(e)
    
    print(f"  sc={len(sc)}, futoi={len(futoi)}, hi2={len(hi2)}", end="")
    
    # Merge
    if sc.empty or futoi.empty:
        return None, "empty"
    
    merged = sc.merge(futoi, on='dt', how='inner')
    if merged.empty:
        return None, "no overlap"
    
    if not hi2.empty:
        merged2 = merged.merge(hi2, on='dt', how='left')
        hi2_cols = [c for c in merged2.columns if c.startswith('hhi_')]
        for c in hi2_cols:
            merged2[c] = merged2[c].fillna(0)
        merged = merged2
    
    return merged, None


def compute_correlations(merged, ticker):
    """Compute all pairwise correlations."""
    # Price returns
    merged['ret'] = merged['close'].pct_change()
    merged['ret_next'] = merged['ret'].shift(-1)
    merged['ytd_ret'] = (merged['close'] / merged['close'].iloc[0] - 1) * 100
    
    # FUTOI changes
    merged['yur_net_chg'] = merged['yur_net'].diff()
    
    results = {
        'n_days': len(merged),
        'date_range': f"{merged['dt'].min()} → {merged['dt'].max()}",
        'close_min': float(merged['close'].min()),
        'close_max': float(merged['close'].max()),
        'ytd_return_pct': float((merged['close'].iloc[-1] / merged['close'].iloc[0] - 1) * 100),
    }
    
    # === Supercandles features → price return ===
    sc_cols = ['disb_avg', 'net_vol_pct_avg', 'vol_b_ratio_avg', 'oi_change_max', 'volume', 'net_vol_sum', 'n_bars_5m']
    for col in sc_cols:
        if col in merged.columns and merged[col].notna().sum() > 10:
            results[f'sc_{col}_ret'] = float(merged[col].corr(merged['ret']))
    
    # === Supercandles features → YUR_net ===
    for col in sc_cols:
        if col in merged.columns and merged[col].notna().sum() > 10:
            results[f'sc_{col}_yur'] = float(merged[col].corr(merged['yur_net']))
    
    # === Supercandles features → ret_next (lagged) ===
    for col in sc_cols:
        if col in merged.columns and merged[col].notna().sum() > 10:
            results[f'sc_{col}_ret_next'] = float(merged[col].corr(merged['ret_next']))
    
    # === YUR_net → price (как в 087) ===
    results['yur_net_price'] = float(merged['yur_net'].corr(merged['close']))
    results['yur_chg_ret'] = float(merged['yur_net_chg'].corr(merged['ret']))
    
    # === HHI → price ===
    hhi_cols = ['hhi_vol', 'hhi_agr', 'hhi_pas', 'hhi_agr_buy', 'hhi_agr_sell', 'hhi_buy', 'hhi_sell']
    for col in hhi_cols:
        if col in merged.columns and merged[col].notna().sum() > 10:
            results[f'hhi_{col}_price'] = float(merged[col].corr(merged['close']))
            results[f'hhi_{col}_ret'] = float(merged[col].corr(merged['ret']))
    
    # === YUR_net vs HHI ===
    results['yur_net_hhi_vol'] = float(merged['yur_net'].corr(merged['hhi_vol'])) if 'hhi_vol' in merged.columns and merged['hhi_vol'].notna().sum() > 10 else None
    
    return results


def main():
    client = get_client()
    all_results = {}
    
    for ticker in TICKERS:
        print(f"Processing {ticker}...", end=" ")
        merged, err = load_data(client, ticker)
        if err:
            print(f"SKIP: {err}")
            continue
        if merged is None or len(merged) < 20:
            print(f"SKIP: {len(merged) if merged is not None else 0} days")
            continue
        
        corrs = compute_correlations(merged, ticker)
        all_results[ticker] = corrs
        print(f"{len(merged)} days, YUR→price={corrs['yur_net_price']:.3f}")
    
    # Print summary
    print("\n" + "=" * 110)
    print("CORRELATION SUMMARY — Supercandles + FUTOI + HI2")
    print("=" * 110)
    
    headers = ['Ticker', 'Days', 'YUR→Prc', 'Disb→Ret', 'NetVol→Ret', 'VolRat→Ret', 'HHI→Prc', 'Disb→R_t+1', 'NetVol→R_t+1']
    print(f"{headers[0]:<8} {headers[1]:<6} {headers[2]:<10} {headers[3]:<11} {headers[4]:<12} {headers[5]:<11} {headers[6]:<10} {headers[7]:<11} {headers[8]:<12}")
    print("-" * 110)
    
    for t in TICKERS:
        if t not in all_results:
            continue
        r = all_results[t]
        row = [
            t,
            r['n_days'],
            r.get('yur_net_price', '-'),
            r.get('sc_disb_avg_ret', '-'),
            r.get('sc_net_vol_pct_avg_ret', '-'),
            r.get('sc_vol_b_ratio_avg_ret', '-'),
            r.get('hhi_hhi_vol_price', '-'),
            r.get('sc_disb_avg_ret_next', '-'),
            r.get('sc_net_vol_sum_ret_next', '-'),
        ]
        
        def fmt(v):
            if isinstance(v, (int, float, np.floating)):
                return f"{float(v):+.4f}" if v != '-' else '-'
            return str(v)
        
        print(f"{t:<8} {row[1]:<6} {fmt(row[2]):<10} {fmt(row[3]):<11} {fmt(row[4]):<12} {fmt(row[5]):<11} {fmt(row[6]):<10} {fmt(row[7]):<11} {fmt(row[8]):<12}")
    
    # Extra: YUR vs HHI
    print("\n--- YUR_net vs HHI_vol ---")
    for t in TICKERS:
        if t in all_results and all_results[t].get('yur_net_hhi_vol') is not None:
            print(f"  {t:<8} YUR↔HHI_vol={all_results[t]['yur_net_hhi_vol']:+.4f}")
    
    # Export
    class NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer, np.floating)):
                return float(obj)
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, pd.Timestamp):
                return str(obj)
            return super().default(obj)
    
    with open('/home/user/sc_futoi_hi2_correlation.json', 'w') as f:
        json.dump(all_results, f, indent=2, cls=NpEncoder)
    
    print(f"\nFull results saved to /home/user/sc_futoi_hi2_correlation.json")


if __name__ == '__main__':
    main()
