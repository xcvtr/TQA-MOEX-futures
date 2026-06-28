#!/usr/bin/env python3
"""Stop Hunt Detection Test on MOEX Futures"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json

CLICKHOUSE_HOST = "http://10.0.0.60:8123"
DB = "moex"
TICKERS = ["Si", "GZ", "CR", "RB"]
START = "2024-10-01"
END = datetime.now().strftime("%Y-%m-%d")
LOOKBACK = 20
FORWARD_BARS = [1, 3, 6, 12]
RETRACE_THRESHOLD = 0.3  # close must retrace 30% of the breakout bar's range

def query_clickhouse(ticker):
    sql = f"""
    SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
           argMax(pr_open, SYSTIME) as opn,
           argMax(pr_high, SYSTIME) as hi,
           argMax(pr_low, SYSTIME) as lo,
           argMax(pr_close, SYSTIME) as prc,
           sum(vol) as vol
    FROM moex.tradestats_fo
    WHERE secid LIKE '{ticker}%'
      AND SYSTIME >= '{START}'
    GROUP BY bt
    ORDER BY bt
    FORMAT CSVWithNames
    """
    r = requests.post(CLICKHOUSE_HOST, data=sql, timeout=60)
    r.raise_for_status()
    lines = r.text.strip().split('\n')
    if len(lines) < 2:
        return pd.DataFrame()
    data = []
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) < 6:
            continue
        bt = parts[0].strip('"\' ')
        opn = float(parts[1])
        hi = float(parts[2])
        lo = float(parts[3])
        prc = float(parts[4])
        vol = float(parts[5])
        data.append({'bt': bt, 'opn': opn, 'hi': hi, 'lo': lo, 'prc': prc, 'vol': vol})
    df = pd.DataFrame(data)
    if len(df) == 0:
        return df
    df['bt'] = pd.to_datetime(df['bt'])
    return df

def detect_false_breakouts(df):
    """Detect false breakouts (stop hunts) on 20-bar lookback."""
    signals = []
    n = len(df)
    if n < LOOKBACK + 1:
        return signals
    for i in range(LOOKBACK, n):
        hi_20 = df['hi'].iloc[i-LOOKBACK:i].max()
        lo_20 = df['lo'].iloc[i-LOOKBACK:i].min()
        bar = df.iloc[i]
        range_ = bar['hi'] - bar['lo']
        if range_ == 0:
            continue
        # False breakout up: price breaks above 20-bar high but closes back inside
        if bar['hi'] > hi_20 and bar['prc'] < bar['hi'] - RETRACE_THRESHOLD * range_:
            signals.append({
                'idx': i,
                'time': bar['bt'],
                'type': 'short',  # reversal down
                'price': bar['prc'],
                'break_high': bar['hi'],
                'break_low': bar['lo'],
                'hi_20': hi_20,
                'range': range_
            })
        # False breakout down: price breaks below 20-bar low but closes back inside
        if bar['lo'] < lo_20 and bar['prc'] > bar['lo'] + RETRACE_THRESHOLD * range_:
            signals.append({
                'idx': i,
                'time': bar['bt'],
                'type': 'long',  # reversal up
                'price': bar['prc'],
                'break_high': bar['hi'],
                'break_low': bar['lo'],
                'lo_20': lo_20,
                'range': range_
            })
    return signals

def calc_forward_returns(df, signals, horizons):
    """Calculate forward returns for each signal at each horizon."""
    n = len(df)
    results = []
    for sig in signals:
        idx = sig['idx']
        entry = sig['price']
        sig_type = sig['type']  # 'long' or 'short'
        for h in horizons:
            fwd_idx = idx + h
            if fwd_idx >= n:
                continue
            if sig_type == 'long':
                ret = (df['prc'].iloc[fwd_idx] - entry) / entry
            else:  # short
                ret = (entry - df['prc'].iloc[fwd_idx]) / entry
            results.append({
                'type': sig_type,
                'horizon': h,
                'return': ret * 100  # percentage
            })
    return results

def random_baseline(df, n_signals, horizons, seed=42):
    """Generate random entry signals for baseline comparison."""
    np.random.seed(seed)
    n = len(df)
    results = []
    for _ in range(n_signals):
        idx = np.random.randint(LOOKBACK, n - max(horizons) - 1)
        sig_type = np.random.choice(['long', 'short'])
        entry = df['prc'].iloc[idx]
        for h in horizons:
            fwd_idx = idx + h
            if fwd_idx >= n:
                continue
            if sig_type == 'long':
                ret = (df['prc'].iloc[fwd_idx] - entry) / entry
            else:
                ret = (entry - df['prc'].iloc[fwd_idx]) / entry
            results.append({
                'type': sig_type,
                'horizon': h,
                'return': ret * 100
            })
    return results

def calc_stats(results, label="signal"):
    """Calculate WR, mean return, NetP80 from results list."""
    if not results:
        return None
    df = pd.DataFrame(results)
    stats = []
    for h, grp in df.groupby('horizon'):
        rets = grp['return'].values
        wr = np.mean(rets > 0) * 100
        mean_ret = np.mean(rets)
        # NetP80: average of best 80% - average of worst 80% (net of tails)
        sorted_rets = np.sort(rets)
        n80 = int(len(sorted_rets) * 0.8)
        best80 = sorted_rets[-n80:].mean()
        worst80 = sorted_rets[:n80].mean()
        net80 = best80 - worst80
        stats.append({
            'horizon': h,
            'n_signals': len(rets),
            'wr_pct': round(wr, 2),
            'mean_ret_pct': round(mean_ret, 4),
            'net80_pct': round(net80, 4),
        })
    return stats

def run_test():
    all_results = {}
    for ticker in TICKERS:
        print(f"\n{'='*60}")
        print(f"Processing {ticker}...")
        print(f"{'='*60}")
        df = query_clickhouse(ticker)
        if len(df) < LOOKBACK + 20:
            print(f"  Not enough data ({len(df)} bars), skipping.")
            all_results[ticker] = None
            continue
        print(f"  Bars: {len(df)}  ({df['bt'].iloc[0].date()} to {df['bt'].iloc[-1].date()})")
        
        signals = detect_false_breakouts(df)
        print(f"  False breakouts detected: {len(signals)}")
        
        if len(signals) < 5:
            print(f"  Too few signals ({len(signals)}), skipping stats.")
            all_results[ticker] = {"n_signals": len(signals), "signal_stats": None, "random_stats": None}
            continue
        
        signal_results = calc_forward_returns(df, signals, FORWARD_BARS)
        signal_stats = calc_stats(signal_results, "stop_hunt")
        
        # Random baseline with same number of signals
        random_results = random_baseline(df, len(signals), FORWARD_BARS)
        random_stats = calc_stats(random_results, "random")
        
        all_results[ticker] = {
            "n_signals": len(signals),
            "signal_stats": signal_stats,
            "random_stats": random_stats
        }
        
        print(f"\n  --- Signal Stats ---")
        for s in signal_stats:
            print(f"  Horizon {s['horizon']:2d}: N={s['n_signals']:4d}  WR={s['wr_pct']:5.2f}%  Mean={s['mean_ret_pct']:.4f}%  Net80={s['net80_pct']:.4f}%")
        
        print(f"\n  --- Random Baseline ---")
        for s in random_stats:
            print(f"  Horizon {s['horizon']:2d}: N={s['n_signals']:4d}  WR={s['wr_pct']:5.2f}%  Mean={s['mean_ret_pct']:.4f}%  Net80={s['net80_pct']:.4f}%")
    
    return all_results

def print_summary(all_results):
    print(f"\n\n{'='*70}")
    print("FINAL SUMMARY — STOP HUNT DETECTION ON MOEX FUTURES")
    print(f"{'='*70}")
    print(f"Period: {START} to {END}")
    print(f"Lookback: {LOOKBACK} bars | Retrace threshold: {RETRACE_THRESHOLD}")
    print()
    
    header = f"{'Ticker':<8} {'H':>3} {'N_sig':>6} {'WR_sig':>8} {'Mean_sig':>10} {'Net80_sig':>10} {'WR_rnd':>8} {'Mean_rnd':>10} {'Net80_rnd':>10} {'Edge?':>6}"
    print(header)
    print("-" * len(header))
    
    any_edge = False
    for ticker in TICKERS:
        res = all_results.get(ticker)
        if res is None or res['signal_stats'] is None:
            print(f"{ticker:<8} {'—':>3} {'—':>6} {'—':>8} {'—':>10} {'—':>10} {'—':>8} {'—':>10} {'—':>10} {'—':>6}")
            continue
        for ss, rs in zip(res['signal_stats'], res['random_stats']):
            edge = "✅" if ss['wr_pct'] > 52 and ss['net80_pct'] > 0 else "❌"
            if ss['wr_pct'] > 52 and ss['net80_pct'] > 0:
                any_edge = True
            print(f"{ticker:<8} {ss['horizon']:>3} {ss['n_signals']:>6} {ss['wr_pct']:>7.2f}% {ss['mean_ret_pct']:>9.4f}% {ss['net80_pct']:>9.4f}% {rs['wr_pct']:>7.2f}% {rs['mean_ret_pct']:>9.4f}% {rs['net80_pct']:>9.4f}% {edge:>6}")
    print()
    if not any_edge:
        print("CONCLUSION: NO ticker shows WR > 52% AND NetP80 > 0 across any horizon.")
        print("False breakouts do NOT provide a reliable edge on MOEX futures (Si, GZ, CR, RB).")
    else:
        print("CONCLUSION: Some tickers show edge — see above.")

if __name__ == "__main__":
    results = run_test()
    print_summary(results)
