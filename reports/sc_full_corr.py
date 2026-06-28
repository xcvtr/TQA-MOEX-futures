#!/usr/bin/env python3
"""
Full correlation analysis: Supercandles features vs price, with lags.
Goal: find features that can serve as trading signals.

Data sources:
  - moex.supercandles_fo (5m bars → daily OHLC + features)
  - moex.futoi (YUR net position)
  - moex.hi2_fo (HHI concentration, pivoted)

For each ticker, for each feature, compute:
  1. Correlation(feature_t, return_t+1) — lagged predictive power
  2. Correlation(feature_t, return_t) — contemporaneous
  3. Stability: correlation by year (2025, 2026)
  4. Simple signal test: long when feature > threshold, short when < threshold
"""

import json
import sys
from datetime import datetime

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

# Все тикеры, где есть supercandles + FUTOI
TICKERS = [
    "Si", "GL", "CR", "BR", "Eu", "GD", "SR", "AF", "RB",
    "PD", "PT", "RI", "ED", "NG", "CC", "SF", "MM", "NM",
    "MX", "NR", "SV", "BM", "NA", "GZ", "GK", "SS", "YD",
    "LK", "RM", "RN", "VT", "UC", "VB", "TN", "SP", "SN",
    "CE", "MC", "MG", "ME", "PI", "RL", "X5", "FF", "EH",
    "BT", "NV", "OJ", "KC", "IB", "HS", "HY", "CH", "DX",
]

# Supercandles features that could be signals
SC_FEATURES = [
    "disb_mean",        # агрессивный дизбаланс (mean)
    "disb_std",         # волатильность дизбаланса
    "disb_last",        # последний дизбаланс в дне
    "net_vol_pct",      # чистый объём (% от total)
    "vol_b_ratio",      # доля объёма покупок
    "trades_b_ratio",   # доля сделок покупок
    "val_b_ratio",      # доля стоимости покупок
    "oi_change",        # изменение OI
    "pr_range_pct",     # внутридневной размах
    "pr_change_pct",    # внутридневное изменение
    "net_vol",          # чистый объём (abs)
    "vwap",             # средневзвешенная цена
]


def load_daily_data(client, ticker):
    """Load daily aggregated data from supercandles + futoi + hi2."""
    
    # Supercandles: daily aggregation
    sc_q = f"""
    SELECT 
        tradedate as dt,
        argMax(pr_close, tradetime) as close,
        argMax(pr_open, tradetime) as open,
        max(pr_high) as high,
        min(pr_low) as low,
        sum(vol_sum) as volume,
        avg(disb_mean) as disb_mean,
        avg(disb_std) as disb_std,
        argMax(disb_last, tradetime) as disb_last,
        avg(net_vol_pct) as net_vol_pct,
        avg(vol_b_ratio) as vol_b_ratio,
        avg(trades_b_ratio) as trades_b_ratio,
        avg(val_b_ratio) as val_b_ratio,
        max(oi_change) as oi_change,
        avg(pr_range_pct) as pr_range_pct,
        avg(pr_change_pct) as pr_change_pct,
        sum(net_vol) as net_vol,
        avg(vwap) as vwap,
        avg(vwap_b) as vwap_b,
        avg(vwap_s) as vwap_s,
        argMax(im, tradetime) as im,
        count() as n_bars_5m
    FROM moex.supercandles_fo
    WHERE ticker = '{ticker}'
    GROUP BY tradedate
    ORDER BY dt
    """
    
    # FUTOI: YUR daily
    futoi_q = f"""
    SELECT 
        tradedate as dt,
        argMax(pos, tradetime) as yur_net,
        argMax(pos_long_num, tradetime) as yur_long_acc,
        argMax(pos_short_num, tradetime) as yur_short_acc
    FROM moex.futoi
    WHERE ticker = '{ticker}' AND clgroup = 'YUR'
    GROUP BY tradedate
    ORDER BY dt
    """
    
    # HI2: pivoted by metric
    hi2_ticker = ticker
    if ticker == 'CR':
        hi2_ticker = 'CNY'
    elif ticker in ('GL', 'Si', 'BR', 'Eu', 'GD', 'SR', 'AF', 'RB', 
                    'PD', 'PT', 'RI', 'ED', 'NG', 'CC', 'SF', 'MM', 'NM',
                    'MX', 'NR', 'SV', 'BM', 'NA', 'GZ', 'GK', 'SS', 'YD',
                    'LK', 'RM', 'RN', 'UC', 'VB', 'TN', 'SP', 'SN',
                    'CE', 'MC', 'MG', 'ME', 'PI', 'RL', 'X5', 'FF', 'EH',
                    'BT', 'NV', 'OJ', 'KC', 'IB', 'HS', 'HY', 'CH', 'DX',
                    'VT'):
        pass  # совпадает
    # остальные
    
    hi2_q = f"""
    SELECT 
        tradedate as dt,
        argMax(if(metric='hhi_volume', value, NULL), tradetime) as hhi_vol,
        argMax(if(metric='hhi_agressive', value, NULL), tradetime) as hhi_agr,
        argMax(if(metric='hhi_passive', value, NULL), tradetime) as hhi_pas,
        argMax(if(metric='hhi_agressive_buy', value, NULL), tradetime) as hhi_agr_buy,
        argMax(if(metric='hhi_agressive_sell', value, NULL), tradetime) as hhi_agr_sell,
        argMax(if(metric='hhi_buy', value, NULL), tradetime) as hhi_buy,
        argMax(if(metric='hhi_sell', value, NULL), tradetime) as hhi_sell,
        argMax(if(metric='hhi_netflow_buy', value, NULL), tradetime) as hhi_net_buy,
        argMax(if(metric='hhi_netflow_sell', value, NULL), tradetime) as hhi_net_sell
    FROM moex.hi2_fo
    WHERE asset_code = '{hi2_ticker}'
      AND metric IN ('hhi_volume','hhi_agressive','hhi_passive',
                     'hhi_agressive_buy','hhi_agressive_sell',
                     'hhi_buy','hhi_sell',
                     'hhi_netflow_buy','hhi_netflow_sell')
    GROUP BY tradedate
    ORDER BY dt
    """
    
    try:
        sc = client.query_df(sc_q)
        futoi = client.query_df(futoi_q)
        hi2 = client.query_df(hi2_q)
    except Exception as e:
        return None, str(e)
    
    if sc.empty or futoi.empty:
        return None, f"empty: sc={len(sc)}, futoi={len(futoi)}"
    
    merged = sc.merge(futoi, on='dt', how='inner')
    
    if not hi2.empty:
        merged = merged.merge(hi2, on='dt', how='left')
        for c in [x for x in merged.columns if x.startswith('hhi_')]:
            merged[c] = merged[c].fillna(0)
    
    return merged, None


def analyze_ticker(merged, ticker):
    """Full correlation analysis for one ticker."""
    
    df = merged.copy()
    df['ret'] = df['close'].pct_change() * 100
    df['ret_next'] = df['ret'].shift(-1)
    df['ret_prev'] = df['ret'].shift(1)
    df['year'] = pd.to_datetime(df['dt']).dt.year
    df['range_pct'] = (df['high'] - df['low']) / df['open'] * 100
    
    # FUTOI features
    for c in ['yur_long_acc', 'yur_short_acc']:
        if c in df.columns:
            df[c + '_chg'] = df[c].diff()
    
    # Lists of features to test
    feat_candidates = {}
    for f in SC_FEATURES:
        if f in df.columns:
            feat_candidates[f'sc_{f}'] = f
    
    futoi_feats = ['yur_net']
    for f in futoi_feats:
        if f in df.columns:
            feat_candidates[f'futoi_{f}'] = f
    
    hhi_feats = ['hhi_vol', 'hhi_agr', 'hhi_pas', 'hhi_agr_buy', 'hhi_agr_sell', 'hhi_buy', 'hhi_sell']
    for f in hhi_feats:
        if f in df.columns and df[f].notna().sum() > 10:
            feat_candidates[f'hhi_{f}'] = f
    
    # Price momentum
    df['ret_5d'] = df['close'].pct_change(5) * 100
    df['volume_ma5'] = df['volume'].rolling(5).mean()
    df['vol_ratio'] = df['volume'] / df['volume_ma5']
    feat_candidates['ret_5d'] = 'ret_5d'
    feat_candidates['vol_ratio'] = 'vol_ratio'
    
    results = {
        'ticker': ticker,
        'n_days': len(df),
        'date_range': f"{df['dt'].min()} → {df['dt'].max()}",
        'close_mean': float(df['close'].mean()),
        'close_std': float(df['close'].std()),
    }
    
    # 1. Contemporaneous correlation: feature_t vs ret_t
    # 2. Lagged correlation: feature_t vs ret_t+1 (predictive!)
    # 3. Stability: by year
    
    for name, col in feat_candidates.items():
        s = df[col].dropna()
        r = df['ret'].loc[s.index].dropna()
        rn = df['ret_next'].loc[s.index].dropna()
        
        if len(s) < 20 or len(r) < 20:
            continue
        
        # Contemporaneous
        c_corr = float(s.corr(r))
        # Lagged (predictive)
        l_corr = float(s.corr(rn))
        
        # Year-by-year stability
        years = {}
        for yr in sorted(df['year'].unique()):
            mask = df['year'] == yr
            sy = df.loc[mask, col].dropna()
            ry = df.loc[mask, 'ret'].dropna()
            rny = df.loc[mask, 'ret_next'].dropna()
            if len(sy) > 10:
                years[int(yr)] = {
                    'contemp': float(sy.corr(ry)) if len(sy) == len(ry) else None,
                    'lagged': float(sy.corr(rny)) if len(sy) == len(rny) else None,
                    'n': len(sy),
                }
        
        # Simple signal test: long when feature > +1σ, short when < -1σ
        # Returns: avg return per signal day
        mu = s.mean()
        sigma = s.std()
        
        if sigma > 0 and sigma != float('inf'):
            sig_short = s < (mu - sigma)
            sig_long = s > (mu + sigma)
            sig_short_r = df.loc[sig_short.index[sig_short], 'ret_next'].mean() if sig_short.sum() > 0 else None
            sig_long_r = df.loc[sig_long.index[sig_long], 'ret_next'].mean() if sig_long.sum() > 0 else None
            
            sig_short_n = int(sig_short.sum())
            sig_long_n = int(sig_long.sum())
        else:
            sig_short_r = sig_long_r = None
            sig_short_n = sig_long_n = 0
        
        entry = {
            'corr_contemp': c_corr,
            'corr_lagged': l_corr,
            'by_year': years,
            'signal_short_ret': float(sig_short_r) if sig_short_r is not None else None,
            'signal_long_ret': float(sig_long_r) if sig_long_r is not None else None,
            'signal_short_n': sig_short_n,
            'signal_long_n': sig_long_n,
            'mean': float(mu),
            'std': float(sigma),
        }
        
        results[name] = entry
    
    return results


def print_summary(all_results, threshold=0.05):
    """Print compact summary of best correlations."""
    
    print("\n" + "=" * 130)
    print("BEST CORRELATIONS — Supercandles + FUTOI + HI2 features vs price return")
    print("=" * 130)
    
    # Collect all feature entries
    rows = []
    for ticker, data in all_results.items():
        if not data or 'n_days' not in data:
            continue
        for name, entry in data.items():
            if name in ('ticker', 'n_days', 'date_range', 'close_mean', 'close_std'):
                continue
            if not isinstance(entry, dict) or 'corr_lagged' not in entry:
                continue
            
            l_corr = entry.get('corr_lagged')
            c_corr = entry.get('corr_contemp')
            if l_corr is None or abs(l_corr) < threshold:
                continue
            
            ss_r = entry.get('signal_short_ret')
            sl_r = entry.get('signal_long_ret')
            
            # Check stability: consistent sign in both years
            years = entry.get('by_year', {})
            signs_consistent = True
            yr_signs = []
            for yr in sorted(years.keys()):
                v = years[yr].get('lagged')
                if v is not None:
                    yr_signs.append(np.sign(v))
            if len(yr_signs) >= 2:
                signs_consistent = all(s == yr_signs[0] for s in yr_signs)
            
            rows.append({
                'ticker': ticker,
                'feature': name,
                'lagged': l_corr,
                'contemp': c_corr,
                'short_ret': ss_r,
                'long_ret': sl_r,
                'short_n': entry.get('signal_short_n', 0),
                'long_n': entry.get('signal_long_n', 0),
                'consistent': signs_consistent,
                'years': years,
            })
    
    # Sort by |lagged correlation|
    rows.sort(key=lambda r: abs(r['lagged']), reverse=True)
    
    print(f"\nFeatures with |corr_lagged| >= {threshold}:")
    print(f"{'Ticker':<8} {'Feature':<22} {'Lagged':<9} {'Contemp':<9} {'S/RET':<8} {'L/RET':<8} {'S/N':<5} {'L/N':<5} {'Stable':<7}")
    print("-" * 130)
    
    for r in rows:
        c = "✅" if r['consistent'] else "⚠️"
        sr = f"{r['short_ret']:.2f}%" if r['short_ret'] is not None else "-"
        lr = f"{r['long_ret']:.2f}%" if r['long_ret'] is not None else "-"
        print(f"{r['ticker']:<8} {r['feature']:<22} {r['lagged']:+.4f}   {r['contemp']:+.4f}   {sr:<8} {lr:<8} {r['short_n']:<5} {r['long_n']:<5} {c:<7}")
    
    # Print year stability for top features
    print("\n\n--- Year-by-year stability (top features) ---")
    for r in rows[:15]:
        yr_str = ", ".join([
            f"{yr}: {years[yr]['lagged']:.3f}(n={years[yr]['n']})" if years[yr]['lagged'] is not None
            else f"{yr}: None(n={years[yr]['n']})"
            for yr in sorted(r['years'].keys())
        ])
        print(f"  {r['ticker']:<6} {r['feature']:<22} [{yr_str}] {'✅' if r['consistent'] else '⚠️'}")


def main():
    client = get_client()
    all_results = {}
    
    for ticker in TICKERS:
        print(f"\n{datetime.now().strftime('%H:%M:%S')} {ticker}...", end=" ", flush=True)
        
        try:
            df, err = load_daily_data(client, ticker)
            if err:
                print(f"SKIP: {err}")
                continue
        except Exception as e:
            print(f"ERROR loading: {e}")
            continue
        
        if df is None or len(df) < 50:
            print(f"SKIP: insufficient data ({len(df) if df is not None else 0})")
            continue
        
        try:
            results = analyze_ticker(df, ticker)
        except Exception as e:
            print(f"ERROR analyze: {e}")
            import traceback
            traceback.print_exc()
            continue
        
        all_results[ticker] = results
        n_feats = len([k for k in results.keys() if k.startswith(('sc_', 'futoi_', 'hhi_', 'ret_5d', 'vol_ratio'))])
        print(f"OK: {results['n_days']} days, {n_feats} features")
    
    # Print summary
    print_summary(all_results, threshold=0.05)
    
    # Export
    class NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer, np.floating)):
                return float(obj) if not np.isnan(obj) else None
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, pd.Timestamp):
                return str(obj)
            return super().default(obj)
    
    # Clean NaN for JSON
    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [clean(v) for v in obj]
        elif isinstance(obj, float) and np.isnan(obj):
            return None
        return obj
    
    with open('/home/user/sc_full_correlation.json', 'w') as f:
        json.dump(clean(all_results), f, indent=2, cls=NpEncoder)
    
    print(f"\nFull results: /home/user/sc_full_correlation.json ({len(all_results)} tickers)")


if __name__ == '__main__':
    main()
