#!/usr/bin/env python3
"""
Feature combination screening for Supercandles × FUTOI.
Tests ALL pairs (and selected triples) of features as filters.
Goal: find combinations that consistently predict next-day return.
"""

import json
import sys
from itertools import combinations

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

# Тикеры с наибольшим сигналом — сокращённый список
TICKERS = [
    "CR", "NR", "NG", "GL", "SR", "RB", "MM", "VB", "X5", 
    "TN", "SP", "MX", "GZ", "NM", "AF", "RI", "PD", "PT", "ED",
    "CC", "SF", "GK", "OJ", "KC", "FF", "Si", "Eu", "BR", "GD",
]

def load_daily(client, ticker):
    """Load daily from supercandles + futoi, merged."""
    sc_q = f"""
    SELECT 
        tradedate as dt,
        argMax(pr_close, tradetime) as close,
        sum(vol_sum) as volume,
        avg(disb_mean) as disb_mean,
        avg(disb_std) as disb_std,
        argMax(disb_last, tradetime) as disb_last,
        avg(net_vol_pct) as net_vol_pct,
        avg(vol_b_ratio) as vol_b_ratio,
        avg(trades_b_ratio) as trades_b_ratio,
        avg(val_b_ratio) as val_b_ratio,
        max(oi_change) as oi_change,
        argMax(oi_close, tradetime) as oi_close,
        argMax(oi_open, tradetime) as oi_open,
        avg(pr_range_pct) as pr_range_pct,
        avg(pr_change_pct) as pr_change_pct,
        sum(net_vol) as net_vol,
        avg(vwap) as vwap,
        count() as n_bars_5m
    FROM moex.supercandles_fo
    WHERE ticker = '{ticker}'
    GROUP BY tradedate
    ORDER BY dt
    """
    
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
    
    try:
        sc = client.query_df(sc_q)
        futoi = client.query_df(futoi_q)
    except Exception as e:
        return None, str(e)
    
    if sc.empty or futoi.empty:
        return None, "empty"
    
    merged = sc.merge(futoi, on='dt', how='inner')
    return merged, None


def compute_derived(df):
    """Add derived features and returns."""
    d = df.copy()
    d['ret'] = d['close'].pct_change() * 100
    d['ret_next'] = d['ret'].shift(-1)
    d['ret_5d'] = d['close'].pct_change(5) * 100
    d['volume_ma5'] = d['volume'].rolling(5).mean()
    d['vol_ratio'] = d['volume'] / d['volume_ma5']
    
    # OI features
    d['oi_change_pct'] = d['oi_change'] / d['oi_close'].shift(1) * 100
    d['oi_ratio'] = d['oi_change'] / d['volume']
    d['oi_z'] = (d['oi_change'] - d['oi_change'].rolling(20).mean()) / d['oi_change'].rolling(20).std()
    
    # Net vol as % of OI
    d['net_oi_ratio'] = d['net_vol'] / (d['oi_close'] + 1)
    
    # Combined
    d['disb_oi'] = d['disb_mean'] * d['oi_change_pct']
    d['disb_vol'] = d['disb_mean'] * d['vol_ratio']
    d['net_oi_z'] = d['net_vol_pct'] * d['oi_z']
    d['disb_net'] = d['disb_mean'] * d['net_vol_pct']
    
    # YUR net features
    d['yur_net_chg'] = d['yur_net'].diff()
    d['yur_net_z'] = (d['yur_net'] - d['yur_net'].rolling(20).mean()) / d['yur_net'].rolling(20).std()
    d['yur_oi_ratio'] = d['yur_net'] / (d['oi_close'] + 1)
    
    # Volume-weighted features
    d['vwap_disb'] = d['vwap'] * d['disb_mean']
    
    return d


def test_signal(df, long_cond, short_cond, label, min_signals=10):
    """Test a binary signal strategy. long_cond/short_cond are boolean Series."""
    valid = df.dropna(subset=['ret_next'])
    
    long_mask = long_cond.reindex(valid.index).fillna(False)
    short_mask = short_cond.reindex(valid.index).fillna(False)
    
    long_trades = valid[long_mask]
    short_trades = valid[short_mask]
    
    results = {}
    
    if len(long_trades) >= min_signals:
        avg_ret = long_trades['ret_next'].mean()
        wr = (long_trades['ret_next'] > 0).mean() * 100
        results['long'] = {
            'n': len(long_trades),
            'avg_ret_pct': round(float(avg_ret), 4),
            'win_rate_pct': round(float(wr), 1),
            'total_ret_pct': round(float(long_trades['ret_next'].sum()), 2),
        }
    
    if len(short_trades) >= min_signals:
        avg_ret = short_trades['ret_next'].mean()
        wr = (short_trades['ret_next'] > 0).mean() * 100
        results['short'] = {
            'n': len(short_trades),
            'avg_ret_pct': round(float(avg_ret), 4),
            'win_rate_pct': round(float(wr), 1),
            'total_ret_pct': round(float(short_trades['ret_next'].sum()), 2),
        }
    
    return results if results else None


def screen_pairs(df, ticker):
    """Screen all pairs of features with threshold combinations."""
    d = compute_derived(df)
    N = len(d)
    
    # Define features and their directions
    single_features = {
        # Raw supercandles (z-score based)
        'disb_mean_z':        ('disb_mean', 'both'),
        'net_vol_pct_z':      ('net_vol_pct', 'both'),
        'vol_b_ratio_z':      ('vol_b_ratio', 'both'),
        'trades_b_ratio_z':   ('trades_b_ratio', 'both'),
        'oi_change_z':        ('oi_change', 'both'),
        'oi_change_pct_z':    ('oi_change_pct', 'both'),
        'pr_change_pct_z':    ('pr_change_pct', 'both'),
        'pr_range_pct_z':     ('pr_range_pct', 'both'),
        'net_vol_z':          ('net_vol', 'both'),
        'n_bars_5m_z':        ('n_bars_5m', 'both'),
        'volume_z':           ('volume', 'both'),
        'vol_ratio_z':        ('vol_ratio', 'both'),
        'disb_last_z':        ('disb_last', 'both'),
        'disb_std_z':         ('disb_std', 'both'),
        'oi_ratio_z':         ('oi_ratio', 'both'),
        'zip_oi_z':           ('oi_z', 'both'),
        'yur_net_z':          ('yur_net_z', 'both'),
        'yur_net_chg_z':      ('yur_net_chg', 'both'),
        'ret_5d':             ('ret_5d', 'neg'),  # mean reversion
        'yur_oi_ratio_z':     ('yur_oi_ratio', 'both'),
        'net_oi_ratio_z':     ('net_oi_ratio', 'both'),
    }
    
    # Combined (multiplicative) features
    combined_features = {
        'disb_oi_z':          ('disb_oi', 'both'),
        'disb_vol_z':         ('disb_vol', 'both'),
        'net_oi_z_zscore':    ('net_oi_z', 'both'),
        'disb_net_z':         ('disb_net', 'both'),
        'vwap_disb_z':        ('vwap_disb', 'both'),
    }
    
    all_features = {**single_features, **combined_features}
    
    # Compute z-scores for all features
    for name, (col, _) in all_features.items():
        if col in d.columns:
            mu = d[col].mean()
            sigma = d[col].std()
            if sigma > 0:
                d[name] = (d[col] - mu) / sigma
            else:
                d[name] = 0.0
    
    # Drop NaN
    d = d.dropna(subset=['ret_next'] + [name for name, _ in all_features.items() if name in d.columns])
    
    results = {
        'ticker': ticker,
        'n_days': N,
        'date_range': f"{d['dt'].min()} → {d['dt'].max()}",
        'single': {},
        'pairs': {},
        'triples': {},
    }
    
    # === 1. Single feature tests ===
    for name, (col, direction) in all_features.items():
        if name not in d.columns:
            continue
        dz = d[name]
        
        for threshold in [0.5, 1.0, 1.5, 2.0]:
            if direction in ('both', 'pos'):
                cond = dz > threshold
                res = test_signal(d, cond, ~cond, f"{name}>+{threshold}")
                if res and ('long' in res or 'short' in res):
                    results['single'][f"{name}>+{threshold}"] = res
            
            if direction in ('both', 'neg'):
                cond = dz < -threshold
                res = test_signal(d, ~cond, cond, f"{name}<-{threshold}")
                if res and ('short' in res or 'long' in res):
                    results['single'][f"{name}<-{threshold}"] = res
    
    # === 2. Pair tests (AND combinations) — только ключевые фичи ===
    # Упрощённый подход: только порог 1.0, направление фиксировано
    key_feats = {
        'ret_5d': -1,  # mean reversion: high ret → short
        'oi_change_z': None,  # both
        'oi_change_pct_z': None,
        'zip_oi_z': None,
        'disb_mean_z': None,
        'disb_last_z': None,
        'net_vol_pct_z': None,
        'vol_b_ratio_z': None,
        'trades_b_ratio_z': None,
        'pr_change_pct_z': None,
        'volume_z': None,
        'vol_ratio_z': None,
        'yur_net_z': None,
        'yur_net_chg_z': None,
        'disb_oi_z': None,
        'disb_vol_z': None,
        'net_oi_z_zscore': None,
        'disb_net_z': None,
        'net_vol_z': None,
        'oi_ratio_z': None,
        'yur_oi_ratio_z': None,
        'net_oi_ratio_z': None,
    }
    feat_names = [n for n in key_feats if n in d.columns]
    
    for f1, f2 in combinations(feat_names, 2):
        s1 = key_feats[f1] if key_feats[f1] is not None else 1
        s2 = key_feats[f2] if key_feats[f2] is not None else 1
        
        for th in [1.0, 1.5]:
            # Both high (in feature direction)
            c_high = (d[f1] * s1 > th) & (d[f2] * s2 > th)
            c_low = (d[f1] * s1 < -th) & (d[f2] * s2 < -th)
            
            label = f"{f1}*{s1:+}>{th} & {f2}*{s2:+}>{th}"
            res = test_signal(d, c_high, c_low, label, min_signals=8)
            if res:
                results['pairs'][label] = res
    
    # === 3. Triple tests (only on promising tickers) ===
    # ret_5d neg + OI + disb — classic combo
    if 'ret_5d' in d.columns and 'oi_change_z' in d.columns and 'disb_mean_z' in d.columns:
        for sign_ret in [-1]:  # mean reversion: ret_5d negative when positive (short)
            for sign_oi in [-1, 1]:
                for sign_disb in [-1, 1]:
                    for th in [0.5, 1.0]:
                        c1 = d['ret_5d'] * sign_ret > th  
                        c2 = d['oi_change_z'] * sign_oi > th
                        c3 = d['disb_mean_z'] * sign_disb > th
                        
                        triple = c1 & c2 & c3
                        opposite = (~c1) & (~c2) & (~c3)
                        
                        label = f"ret5d*{sign_ret:+}>{th} & oi*{sign_oi:+}>{th} & disb*{sign_disb:+}>{th}"
                        res = test_signal(d, triple, opposite, label, min_signals=5)
                        if res:
                            results['triples'][label] = res
    
    return results


def main():
    client = get_client()
    all_results = {}
    
    for ticker in TICKERS:
        print(f"{ticker}...", end=" ", flush=True)
        df, err = load_daily(client, ticker)
        if err:
            print(f"SKIP ({err})")
            continue
        if len(df) < 50:
            print(f"SKIP ({len(df)} days)")
            continue
        
        try:
            res = screen_pairs(df, ticker)
            n_s = len(res['single'])
            n_p = len(res['pairs'])
            n_t = len(res['triples'])
            print(f"{len(df)}d, single={n_s}, pairs={n_p}, triples={n_t}")
            all_results[ticker] = res
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    # Print summary
    print("\n\n" + "=" * 140)
    print("BEST COMBINATIONS — avg ret per trade > 1.5% AND win rate > 55%")
    print("=" * 140)
    
    best = []
    for ticker, res in all_results.items():
        for cat in ('single', 'pairs', 'triples'):
            for label, signals in res.get(cat, {}).items():
                for side in ('long', 'short'):
                    if side not in signals:
                        continue
                    s = signals[side]
                    if s['avg_ret_pct'] > 1.5 and s['win_rate_pct'] > 55 and s['n'] >= 10:
                        best.append({
                            'ticker': ticker,
                            'cat': cat,
                            'label': label,
                            'side': side,
                            'avg_ret': s['avg_ret_pct'],
                            'wr': s['win_rate_pct'],
                            'n': s['n'],
                            'total': s['total_ret_pct'],
                        })
    
    best.sort(key=lambda x: x['avg_ret'], reverse=True)
    print(f"{'Ticker':<8} {'Cat':<8} {'Label':<55} {'Side':<7} {'AvgRet%':<9} {'WR%':<7} {'N':<5} {'Total%':<9}")
    print("-" * 140)
    for b in best:
        print(f"{b['ticker']:<8} {b['cat']:<8} {b['label']:<55} {b['side']:<7} {b['avg_ret']:.2f}%   {b['wr']:.0f}%   {b['n']:<5} {b['total']:.1f}%")
        if len(best) > 100:
            break
    
    # Also print best single features with OI
    print("\n\n" + "=" * 140)
    print("BEST OI-BASED SIGNALS (oi_change_z, oi_ratio_z, oi_z)")
    print("=" * 140)
    for ticker, res in all_results.items():
        for cat in ('single', 'pairs', 'triples'):
            for label, signals in res.get(cat, {}).items():
                if 'oi' not in label.lower():
                    continue
                for side in ('long', 'short'):
                    if side not in signals:
                        continue
                    s = signals[side]
                    if s['avg_ret_pct'] > 0.8 and s['n'] >= 10:
                        print(f"  {ticker:<6} {cat:<6} {label:<55} {side:<6} avg={s['avg_ret_pct']:.2f}%  wr={s['win_rate_pct']:.0f}%  n={s['n']}")
    
    # Export
    class NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer, np.floating)):
                if np.isnan(obj):
                    return None
                return float(obj)
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, pd.Timestamp):
                return str(obj)
            return super().default(obj)
    
    with open('/home/user/sc_combinations.json', 'w') as f:
        json.dump(all_results, f, indent=1, cls=NpEncoder)
    
    print(f"\n\nFull results: /home/user/sc_combinations.json ({len(all_results)} tickers)")


if __name__ == '__main__':
    main()
