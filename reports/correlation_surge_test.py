#!/usr/bin/env python3
"""
Cross-ticker Correlation Surge Test for MOEX Futures
=====================================================
Pairs: Si vs EU (USD/RUB vs EUR/RUB), BR vs CR (Brent vs Crude), GZ vs AF (Gazprom vs Aeroflot)

Method:
1. rolling_corr(return_A, return_B, 20 bars) — 5-min bars, ~100 min window
2. When correlation drops from >0.8 to <0.3 within ~1 hour (12 bars) → structural shift signal
3. Trade divergence: short the weaker, long the stronger
4. Evaluate forward returns at 3, 6, 12 bars
5. Report winrate per pair — if WR < 52% → no signal
"""

import clickhouse_connect
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys

# ── Config ──────────────────────────────────────────────────────────────────
CLICKHOUSE_HOST = '10.0.0.60'
CLICKHOUSE_PORT = 8123
DB = 'moex'
TABLE = 'tradestats_fo'
START = '2024-10-01'
NOW = datetime.utcnow().strftime('%Y-%m-%d')
ROLLING_WINDOW = 20           # bars
CORR_HIGH_THRESH = 0.8
CORR_LOW_THRESH = 0.3
DROPS_WITHIN_BARS = 12        # ~1 hour at 5-min resolution
FORWARD_BARS = [3, 6, 12]
MIN_WINRATE = 0.52

# Pairs: (pair_name, ticker_A_pattern, ticker_B_pattern, label_A, label_B)
PAIRS = [
    ('Si_EU',  'Si%',  'EURRUBF', 'Si (USD/RUB)', 'EU (EUR/RUB)'),
    ('BR_CR',  'BR%',  'CR%',     'BR (Brent)',   'CR (Crude)'),
    ('GZ_AF',  'GZ%',  'AF%',     'GZ (Gazprom)', 'AF (Aeroflot)'),
]

client = clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT)


def fetch_continuous_5m(ticker_pattern, start=START, end=None):
    """
    Fetch 5-min OHLC using argMax to get a continuous series across contract rolls.
    Uses SECID LIKE '{ticker_pattern}' so all contracts of that family are merged.
    """
    end_sql = f"AND SYSTIME <= '{end}'" if end else ""
    q = f"""
    SELECT
        toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
        argMax(pr_open, SYSTIME)  as pr_open,
        argMax(pr_high, SYSTIME)  as pr_high,
        argMax(pr_low, SYSTIME)   as pr_low,
        argMax(pr_close, SYSTIME) as pr_close,
        argMax(vol, SYSTIME)      as vol
    FROM {DB}.{TABLE}
    WHERE secid LIKE '{ticker_pattern}'
      AND SYSTIME >= '{start}'
      {end_sql}
    GROUP BY bt
    ORDER BY bt
    """
    rows = client.query(q).result_rows
    if not rows:
        print(f"  ⚠ No data for pattern '{ticker_pattern}'")
        return pd.DataFrame()
    
    df = pd.DataFrame(rows, columns=['bt', 'pr_open', 'pr_high', 'pr_low', 'pr_close', 'vol'])
    df['bt'] = pd.to_datetime(df['bt'])
    # Drop rows with NaN close
    df = df.dropna(subset=['pr_close'])
    return df


def compute_pair_analysis(pair_name, ticker_a, ticker_b, label_a, label_b):
    print(f"\n{'='*70}")
    print(f"  PAIR: {pair_name}  —  {label_a} vs {label_b}")
    print(f"{'='*70}")
    
    # 1. Fetch data
    df_a = fetch_continuous_5m(ticker_a)
    df_b = fetch_continuous_5m(ticker_b)
    
    if df_a.empty or df_b.empty:
        print(f"  ❌ Skipping: one or both series empty")
        return None
    
    # 2. Merge on time
    merged = pd.merge(df_a[['bt', 'pr_close', 'vol']], 
                      df_b[['bt', 'pr_close', 'vol']],
                      on='bt', suffixes=(f'_{pair_name}_a', f'_{pair_name}_b'))
    merged = merged.sort_values('bt').reset_index(drop=True)
    
    print(f"  Data points (merged): {len(merged)}")
    print(f"  Date range: {merged['bt'].min()} -> {merged['bt'].max()}")
    
    if len(merged) < ROLLING_WINDOW + 20:
        print(f"  ❌ Skipping: too few data points ({len(merged)})")
        return None
    
    # 3. Compute log returns
    merged['ret_a'] = np.log(merged[f'pr_close_{pair_name}_a'] / merged[f'pr_close_{pair_name}_a'].shift(1))
    merged['ret_b'] = np.log(merged[f'pr_close_{pair_name}_b'] / merged[f'pr_close_{pair_name}_b'].shift(1))
    
    # 4. Rolling correlation (20 bars)
    merged['corr'] = merged['ret_a'].rolling(ROLLING_WINDOW).corr(merged['ret_b'])
    
    # 5. Detect structural shifts: correlation drops from high to low
    #    We look for a bar where corr was > 0.8 within the last DROPS_WITHIN_BARS bars,
    #    and now corr < 0.3
    signals = []
    
    for i in range(ROLLING_WINDOW + DROPS_WITHIN_BARS, len(merged)):
        current_corr = merged.loc[i, 'corr']
        if pd.isna(current_corr):
            continue
        if current_corr >= CORR_LOW_THRESH:
            continue  # hasn't dropped enough
        
        # Check if within last DROPS_WITHIN_BARS bars, corr was > HIGH_THRESH
        lookback_start = max(ROLLING_WINDOW, i - DROPS_WITHIN_BARS)
        lookback = merged.loc[lookback_start:i, 'corr'].dropna()
        if len(lookback) == 0:
            continue
        
        max_recent_corr = lookback.max()
        if max_recent_corr <= CORR_HIGH_THRESH:
            continue  # never was high enough
        
        # Signal detected!
        # Determine which is weaker: negative return = weaker
        # Compare relative strength in the last few bars
        lookback_ret_a = merged.loc[i, 'ret_a']
        lookback_ret_b = merged.loc[i, 'ret_b']
        
        # Direction: if ret_a < ret_b, A is weaker (short A, long B)
        a_is_weaker = lookback_ret_a < lookback_ret_b
        
        signals.append({
            'bt': merged.loc[i, 'bt'],
            'corr_now': round(current_corr, 4),
            'corr_high': round(max_recent_corr, 4),
            'a_is_weaker': a_is_weaker,
            'px_a': merged.loc[i, f'pr_close_{pair_name}_a'],
            'px_b': merged.loc[i, f'pr_close_{pair_name}_b'],
            'ret_a': round(lookback_ret_a * 100, 3),
            'ret_b': round(lookback_ret_b * 100, 3),
        })
    
    print(f"  Signals detected: {len(signals)}")
    
    if len(signals) == 0:
        print(f"  ℹ No correlation drop signals found for this pair")
        return merged, signals, []
    
    # 6. Compute forward returns
    results = []
    for sig in signals:
        sig_idx = merged[merged['bt'] == sig['bt']].index
        if len(sig_idx) == 0:
            continue
        idx = sig_idx[0]
        
        row = {
            'bt': sig['bt'],
            'corr_now': sig['corr_now'],
            'corr_high': sig['corr_high'],
            'a_is_weaker': sig['a_is_weaker'],
            'px_a_entry': sig['px_a'],
            'px_b_entry': sig['px_b'],
            'ret_a_bar': sig['ret_a'],
            'ret_b_bar': sig['ret_b'],
        }
        
        # Direction: if A is weaker → short A, long B
        # So the pair return = ret(B) - ret(A)  (long B, short A)
        # If B is weaker → long A, short B
        # Pair return = ret(A) - ret(B)
        
        for fw in FORWARD_BARS:
            fwd_idx = idx + fw
            if fwd_idx >= len(merged):
                row[f'pair_ret_{fw}b'] = None
                row[f'hit_{fw}b'] = None
                continue
            
            fwd_ret_a = np.log(merged.loc[fwd_idx, f'pr_close_{pair_name}_a'] / sig['px_a'])
            fwd_ret_b = np.log(merged.loc[fwd_idx, f'pr_close_{pair_name}_b'] / sig['px_b'])
            
            if sig['a_is_weaker']:
                # Short A, Long B
                pair_ret = fwd_ret_b - fwd_ret_a
            else:
                # Long A, Short B
                pair_ret = fwd_ret_a - fwd_ret_b
            
            row[f'pair_ret_{fw}b'] = round(pair_ret * 100, 3)
            row[f'hit_{fw}b'] = pair_ret > 0
        
        results.append(row)
    
    df_results = pd.DataFrame(results)
    
    # 7. Compute winrates
    summary = {}
    for fw in FORWARD_BARS:
        col = f'hit_{fw}b'
        valid = df_results[col].dropna()
        if len(valid) > 0:
            wr = valid.mean()
            summary[f'WR_{fw}b'] = round(wr, 4)
            summary[f'N_{fw}b'] = len(valid)
            summary[f'avg_ret_{fw}b'] = round(df_results[f'pair_ret_{fw}b'].dropna().mean(), 4)
        else:
            summary[f'WR_{fw}b'] = None
            summary[f'N_{fw}b'] = 0
            summary[f'avg_ret_{fw}b'] = None
    
    summary['total_signals'] = len(signals)
    summary['is_actionable'] = any(
        summary.get(f'WR_{fw}b', 0) is not None and summary[f'WR_{fw}b'] >= MIN_WINRATE 
        for fw in FORWARD_BARS
    )
    
    # Print summary
    print(f"\n  ┌─ Results ──────────────────────────────────────────────")
    for fw in FORWARD_BARS:
        wr = summary.get(f'WR_{fw}b', 'N/A')
        n = summary.get(f'N_{fw}b', 0)
        avg = summary.get(f'avg_ret_{fw}b', 'N/A')
        if wr is not None and wr > 0:
            flag = " ✅" if wr >= MIN_WINRATE else ""
            print(f"  │ WR_{fw}b = {wr:.1%}  (N={n}, avg_ret={avg}%){flag}")
        else:
            print(f"  │ WR_{fw}b = N/A  (N={n})")
    print(f"  └────────────────────────────────────────────────────────")
    print(f"  Total signals: {summary['total_signals']}")
    print(f"  Actionable:    {summary['is_actionable']}")
    
    return merged, signals, results, summary


def main():
    print(f"╔══════════════════════════════════════════════════════════╗")
    print(f"║   MOEX Cross-Ticker Correlation Surge Test              ║")
    print(f"║   Period: {START} → {NOW}                               ║")
    print(f"║   Window: {ROLLING_WINDOW} bars (5-min), {DROPS_WITHIN_BARS} bar lookback ║")
    print(f"╚══════════════════════════════════════════════════════════╝")
    
    all_pairs_results = {}
    
    for pair_name, ticker_a, ticker_b, label_a, label_b in PAIRS:
        result = compute_pair_analysis(pair_name, ticker_a, ticker_b, label_a, label_b)
        if result is not None:
            merged, signals, results, summary = result
            all_pairs_results[pair_name] = {
                'merged': merged,
                'signals': signals,
                'results': results,
                'summary': summary,
            }
    
    # ── Final summary table ─────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print(f"  FINAL SUMMARY TABLE")
    print(f"{'='*70}")
    
    header = f"{'Pair':<10} {'Signals':>7} {'WR_3b':>8} {'WR_6b':>8} {'WR_12b':>8} {'AvgRet3b':>9} {'AvgRet6b':>9} {'AvgRet12b':>9} {'Actionable':>10}"
    print(header)
    print("-" * len(header))
    
    for pair_name, data in all_pairs_results.items():
        s = data['summary']
        wr3 = f"{s.get('WR_3b', 0):.1%}" if s.get('WR_3b') else "N/A"
        wr6 = f"{s.get('WR_6b', 0):.1%}" if s.get('WR_6b') else "N/A"
        wr12 = f"{s.get('WR_12b', 0):.1%}" if s.get('WR_12b') else "N/A"
        ar3 = f"{s.get('avg_ret_3b', 0):+.3f}" if s.get('avg_ret_3b') is not None else "N/A"
        ar6 = f"{s.get('avg_ret_6b', 0):+.3f}" if s.get('avg_ret_6b') is not None else "N/A"
        ar12 = f"{s.get('avg_ret_12b', 0):+.3f}" if s.get('avg_ret_12b') is not None else "N/A"
        act = "✅ YES" if s.get('is_actionable') else "❌ NO"
        print(f"{pair_name:<10} {s['total_signals']:>7} {wr3:>8} {wr6:>8} {wr12:>8} {ar3:>9} {ar6:>9} {ar12:>9} {act:>10}")
    
    print(f"\n{'='*70}")
    print(f"  VERDICT: {'TRADEABLE (WR >= 52% in at least one forward window)' if any(d['summary']['is_actionable'] for d in all_pairs_results.values()) else 'NO TRADEABLE SIGNAL (all WR < 52%)'}")
    print(f"{'='*70}")
    
    # ── Save to CSV for reference ──────────────────────────────────────
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    for pair_name, data in all_pairs_results.items():
        if data['results']:
            fn = f"/home/user/correlation_surge_{pair_name}_{timestamp}.csv"
            df_out = pd.DataFrame(data['results'])
            df_out.to_csv(fn, index=False)
            print(f"\n  Saved: {fn} ({len(df_out)} signals)")
    
    return all_pairs_results


if __name__ == '__main__':
    results = main()
