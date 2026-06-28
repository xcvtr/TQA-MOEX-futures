#!/usr/bin/env python3
"""
Cross-ticker Correlation Surge — REFINED TEST
==============================================
Tries multiple parameter configurations to find any tradeable signal.
"""

import clickhouse_connect
import pandas as pd
import numpy as np
from datetime import datetime

CLICKHOUSE_HOST = '10.0.0.60'
CLICKHOUSE_PORT = 8123
DB = 'moex'
TABLE = 'tradestats_fo'
START = '2024-10-01'
NOW = datetime.now().strftime('%Y-%m-%d')

client = clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT)


def fetch_5m(ticker_pattern, start=START):
    q = f"""
    SELECT
        toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
        argMax(pr_close, SYSTIME) as pr_close
    FROM {DB}.{TABLE}
    WHERE secid LIKE '{ticker_pattern}'
      AND SYSTIME >= '{start}'
    GROUP BY bt
    ORDER BY bt
    """
    rows = client.query(q).result_rows
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=['bt', 'pr_close'])
    df['bt'] = pd.to_datetime(df['bt'])
    return df.dropna(subset=['pr_close'])


def test_config(pair_name, ticker_a, ticker_b, label_a, label_b,
                rolling_window, corr_high, corr_low, lookback_bars,
                forward_bars, spread_filter=None):
    """Test one configuration and return winrates."""
    df_a = fetch_5m(ticker_a)
    df_b = fetch_5m(ticker_b)
    if df_a.empty or df_b.empty:
        return None
    
    merged = pd.merge(df_a, df_b, on='bt', suffixes=('_a', '_b'))
    merged = merged.sort_values('bt').reset_index(drop=True)
    
    if len(merged) < rolling_window + 50:
        return None
    
    # Returns
    merged['ret_a'] = np.log(merged['pr_close_a'] / merged['pr_close_a'].shift(1))
    merged['ret_b'] = np.log(merged['pr_close_b'] / merged['pr_close_b'].shift(1))
    
    # Rolling correlation
    merged['corr'] = merged['ret_a'].rolling(rolling_window).corr(merged['ret_b'])
    
    # Find signals
    signals = []
    min_idx = rolling_window + lookback_bars
    
    for i in range(min_idx, len(merged)):
        c = merged.loc[i, 'corr']
        if pd.isna(c) or c >= corr_low:
            continue
        
        lookback = merged.loc[max(rolling_window, i-lookback_bars):i, 'corr'].dropna()
        if len(lookback) == 0:
            continue
        
        max_corr = lookback.max()
        if max_corr <= corr_high:
            continue
        
        corr_drop = max_corr - c
        
        # Optional: filter by spread (absolute return difference)
        if spread_filter is not None:
            spread = abs(merged.loc[i, 'ret_a'] - merged.loc[i, 'ret_b'])
            if spread < spread_filter:
                continue
        
        signals.append({
            'idx': i,
            'bt': merged.loc[i, 'bt'],
            'corr_now': c,
            'corr_high': max_corr,
            'corr_drop': corr_drop,
            'px_a': merged.loc[i, 'pr_close_a'],
            'px_b': merged.loc[i, 'pr_close_b'],
            'ret_a': merged.loc[i, 'ret_a'],
            'ret_b': merged.loc[i, 'ret_b'],
        })
    
    if len(signals) < 5:
        return None
    
    # Forward returns
    results = []
    for sig in signals:
        idx = sig['idx']
        row = {'bt': sig['bt'], 'corr_now': sig['corr_now'], 'corr_drop': sig['corr_drop']}
        
        # Direction: if ret_a < ret_b → A weaker → short A, long B
        a_weaker = sig['ret_a'] < sig['ret_b']
        
        for fw in forward_bars:
            fwd_idx = idx + fw
            if fwd_idx >= len(merged):
                row[f'pair_ret_{fw}b'] = None
                continue
            
            fwd_ret_a = np.log(merged.loc[fwd_idx, 'pr_close_a'] / sig['px_a'])
            fwd_ret_b = np.log(merged.loc[fwd_idx, 'pr_close_b'] / sig['px_b'])
            
            if a_weaker:
                pair_ret = fwd_ret_b - fwd_ret_a  # long B, short A
            else:
                pair_ret = fwd_ret_a - fwd_ret_b  # long A, short B
            
            row[f'pair_ret_{fw}b'] = pair_ret * 100
        
        results.append(row)
    
    if not results:
        return None
    
    df_res = pd.DataFrame(results)
    
    config_result = {
        'pair': pair_name,
        'rolling_win': rolling_window,
        'corr_high': corr_high,
        'corr_low': corr_low,
        'lookback': lookback_bars,
        'signals': len(signals),
    }
    
    for fw in forward_bars:
        col = f'pair_ret_{fw}b'
        valid = df_res[col].dropna()
        if len(valid) > 0:
            wr = (valid > 0).mean()
            avg = valid.mean()
            config_result[f'WR_{fw}b'] = round(wr, 4)
            config_result[f'avg_ret_{fw}b'] = round(avg, 4)
        else:
            config_result[f'WR_{fw}b'] = None
            config_result[f'avg_ret_{fw}b'] = None
    
    config_result['best_WR'] = max(
        config_result.get(f'WR_{fw}b', 0) or 0 for fw in forward_bars
    )
    config_result['is_actionable'] = config_result['best_WR'] >= 0.52
    
    return config_result


def main():
    print("=" * 100)
    print("MOEX Cross-Ticker Correlation Surge — PARAMETER SWEEP")
    print("=" * 100)
    
    # Parameter grid
    configs = []
    for rolling_win in [10, 20, 30]:
        for corr_high in [0.7, 0.8, 0.85]:
            for corr_low in [0.2, 0.3, 0.4]:
                for lookback in [6, 12, 24]:
                    configs.append((rolling_win, corr_high, corr_low, lookback))
    
    print(f"Testing {len(configs)} parameter combinations per pair...\n")
    
    # Pairs
    pairs = [
        ('Si_EU',  'Si%',  'EURRUBF', 'Si', 'EU'),
        ('BR_CR',  'BR%',  'CR%',     'BR', 'CR'),
        ('GZ_AF',  'GZ%',  'AF%',     'GZ', 'AF'),
    ]
    
    all_best = []
    
    for pair_name, ticker_a, ticker_b, la, lb in pairs:
        print(f"\n{'─'*100}")
        print(f"PAIR: {pair_name} ({la} vs {lb})")
        print(f"{'─'*100}")
        
        pair_results = []
        total = len(configs)
        
        for idx, (rw, ch, cl, lbk) in enumerate(configs):
            if idx % 20 == 0:
                print(f"  Progress: {idx}/{total}", end='\r')
            
            res = test_config(
                pair_name, ticker_a, ticker_b, la, lb,
                rolling_window=rw, corr_high=ch, corr_low=cl,
                lookback_bars=lbk, forward_bars=[3, 6, 12, 24]
            )
            if res is not None:
                pair_results.append(res)
        
        if pair_results:
            df_pair = pd.DataFrame(pair_results)
            actionable = df_pair[df_pair['is_actionable']]
            
            print(f"  Total configs tested: {len(configs)}")
            print(f"  With signals:        {len(pair_results)}")
            print(f"  Actionable (WR>=52%): {len(actionable)}")
            
            if len(actionable) > 0:
                best = actionable.loc[actionable['best_WR'].idxmax()]
                print(f"\n  ★ BEST ACTIONABLE CONFIG:")
                for k, v in best.items():
                    if not pd.isna(v):
                        print(f"    {k}: {v}")
                all_best.append(best)
            
            # Show top 5 overall
            top5 = df_pair.nlargest(5, 'best_WR')
            print(f"\n  Top 5 configurations by best_WR:")
            cols = ['rolling_win', 'corr_high', 'corr_low', 'lookback', 'signals', 'best_WR', 'is_actionable']
            print(f"    {' | '.join(f'{c:>12}' for c in cols)}")
            for _, row in top5.iterrows():
                vals = [f'{row[c]:>12.4f}' if isinstance(row[c], float) else f'{str(row[c]):>12}' for c in cols]
                print(f"    {' | '.join(vals)}")
        else:
            print("  ❌ No configs with enough signals")
    
    # Final verdict
    print(f"\n\n{'='*100}")
    print("OVERALL VERDICT")
    print(f"{'='*100}")
    
    if all_best:
        print("\n  Tradeable configurations found:")
        for row in all_best:
            print(f"  ★ {row['pair']}: rolling={row['rolling_win']}, "
                  f"corr_high={row['corr_high']}, corr_low={row['corr_low']}, "
                  f"lookback={row['lookback']}, signals={row['signals']}, "
                  f"best_WR={row['best_WR']:.1%}")
    else:
        print("\n  ❌ NO tradeable configuration found for any pair across the entire parameter sweep.")
        print("     The cross-ticker correlation drop strategy does NOT work on MOEX futures")
        print("     under any reasonable parameterization.")
    
    print(f"\n{'='*100}")


if __name__ == '__main__':
    main()
