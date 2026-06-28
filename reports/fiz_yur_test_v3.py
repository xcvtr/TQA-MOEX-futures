#!/usr/bin/env python3
"""
FIZ/YUR Divergence Test v3 — Lag, Cooldown, Rank-based
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
    oi_query = f"""
    SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
    FROM moex.prices_5m_oi
    WHERE symbol = '{ticker}' AND time >= '{START_DATE}'
    ORDER BY time FORMAT TabSeparatedWithNames
    """
    oi_raw = query_ch(oi_query)
    lines = oi_raw.strip().split('\n')
    if len(lines) <= 1: return None
    
    data_rows = []
    for line in lines[1:]:
        if not line.strip(): continue
        parts = line.split('\t')
        if len(parts) >= 6:
            data_rows.append({
                'time': parts[0],
                'fiz_buy': int(parts[1]), 'fiz_sell': int(parts[2]),
                'yur_buy': int(parts[3]), 'yur_sell': int(parts[4]),
                'total_oi': int(parts[5]),
            })
    df = pd.DataFrame(data_rows)
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    
    price_query = f"""
    SELECT time, close FROM moex.prices_5m
    WHERE symbol = '{ticker}' AND time >= '{START_DATE}'
    ORDER BY time FORMAT TabSeparatedWithNames
    """
    price_raw = query_ch(price_query)
    lines = price_raw.strip().split('\n')
    if len(lines) <= 1: return None
    
    price_rows = []
    for line in lines[1:]:
        if not line.strip(): continue
        parts = line.split('\t')
        if len(parts) >= 2 and parts[1] != '\\N' and parts[1] != '':
            price_rows.append({'time': parts[0], 'close': float(parts[1])})
    
    price_df = pd.DataFrame(price_rows)
    price_df['time'] = pd.to_datetime(price_df['time'])
    price_df.set_index('time', inplace=True)
    price_df = price_df[~price_df.index.duplicated(keep='first')]
    
    merged = df.join(price_df, how='inner')
    return merged

def run_test_v3(ticker):
    df = load_data(ticker)
    if df is None or len(df) < 200:
        return None
    
    print(f"  {ticker}: {len(df)} rows")
    
    df['fiz_net'] = (df['fiz_buy'] - df['fiz_sell']) / df['total_oi']
    df['yur_net'] = (df['yur_buy'] - df['yur_sell']) / df['total_oi']
    df['dfiz'] = df['fiz_net'].diff()
    df['dyur'] = df['yur_net'].diff()
    df = df.dropna(subset=['dfiz', 'dyur'])
    
    df['dfiz_z'] = (df['dfiz'] - df['dfiz'].mean()) / df['dfiz'].std()
    df['dyur_z'] = (df['dyur'] - df['dyur'].mean()) / df['dyur'].std()
    
    # Signal definitions
    sig_05z = (df['dfiz'] * df['dyur'] < 0) & (df['dfiz_z'].abs() >= 0.5) & (df['dyur_z'].abs() >= 0.5)
    sig_10z = (df['dfiz'] * df['dyur'] < 0) & (df['dfiz_z'].abs() >= 1.0) & (df['dyur_z'].abs() >= 1.0)
    sig_15z = (df['dfiz'] * df['dyur'] < 0) & (df['dfiz_z'].abs() >= 1.5) & (df['dyur_z'].abs() >= 1.5)
    
    # Rank-based divergence score
    fiz_rank = df['dfiz'].rank(pct=True)
    yur_rank = df['dyur'].rank(pct=True)
    div_score = np.abs(fiz_rank - 0.5) * np.abs(yur_rank - 0.5) * np.sign(df['dfiz'] * df['dyur'] * -1)
    sig_score_top = div_score > div_score.quantile(0.85)
    
    # Cooldown on z05 signals
    sig_cooldown = pd.Series(False, index=df.index)
    last_signal_bar = -5
    for i, (idx, val) in enumerate(sig_05z.items()):
        if val and (i - last_signal_bar) >= 5:
            sig_cooldown[idx] = True
            last_signal_bar = i
    
    # Lagged signals
    sig_lag1 = sig_05z.shift(1).fillna(False)
    sig_lag2 = sig_05z.shift(2).fillna(False)
    sig_lag3 = sig_05z.shift(3).fillna(False)
    
    trade_dir = np.sign(df['dyur'])
    
    signal_cols = {
        'z05': sig_05z, 'z10': sig_10z, 'z15': sig_15z,
        'score_top15': sig_score_top, 'cooldown5': sig_cooldown,
        'lag1': sig_lag1, 'lag2': sig_lag2, 'lag3': sig_lag3,
    }
    
    # Pre-compute forward returns
    for fwd in [3, 6, 12]:
        df[f'fwd_ret_{fwd}'] = df['close'].pct_change(fwd).shift(-fwd)
    
    all_stats = {}
    
    for name, sig in signal_cols.items():
        n_signals = sig.sum()
        all_stats[name] = {'n_signals': int(n_signals)}
        
        for fwd in [3, 6, 12]:
            mask = sig & df[f'fwd_ret_{fwd}'].notna()
            n = mask.sum()
            
            if n < 10:
                all_stats[name][fwd] = {'count': n, 'win_rate': 0, 'wins': 0, 'avg_ret': 0}
                continue
            
            strat_ret = trade_dir[mask] * df.loc[mask, f'fwd_ret_{fwd}']
            wins = (strat_ret > 0).sum()
            wr = wins / n * 100
            avg = strat_ret.mean() * 100
            
            all_stats[name][fwd] = {
                'count': n, 'wins': int(wins),
                'win_rate': round(wr, 1), 'avg_ret': round(avg, 4),
            }
        
        best_wr = max(
            all_stats[name][f]['win_rate']
            for f in [3, 6, 12]
            if all_stats[name][f]['count'] >= 10
        ) if any(all_stats[name][f]['count'] >= 10 for f in [3, 6, 12]) else 0
        
        print(f"    {name:>12}: n_sig={n_signals:5d}", end="")
        for fwd in [3, 6, 12]:
            s = all_stats[name][fwd]
            if s['count'] >= 10:
                print(f"  fwd{fwd}: Sig={s['count']:4d} WR={s['win_rate']:5.1f}% Avg={s['avg_ret']:+.4f}%", end="")
        print(f"  best_WR={best_wr:5.1f}%")
    
    return all_stats

def main():
    print(f"FIZ/YUR Divergence Test v3 — Lag, Cooldown, Rank-based")
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"{'='*90}")
    
    for ticker in TICKERS:
        print(f"\n--- {ticker} ---")
        run_test_v3(ticker)

if __name__ == '__main__':
    main()
