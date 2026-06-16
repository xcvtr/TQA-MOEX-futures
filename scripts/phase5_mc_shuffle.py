#!/usr/bin/env python3
"""
Audit 3: Monte Carlo shuffle of Phase 5 signals.
Shuffles score column in time (preserving distribution, destroying temporal signal),
then runs simulation 30 times. Compares real return vs shuffled distribution.
"""
import json, sys, os, random
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.dirname(__file__))
from phase5_walkforward import (
    PORTFOLIO, INITIAL_CAPITAL, precompute_signals, simulate_period,
    TRAIN_END, TEST_END
)

N_SHUFFLES = 30
RANDOM_SEED = 42

def shuffle_signals(signals, seed):
    """
    For each ticker and each pattern, shuffle the score column in time.
    This preserves the score distribution but destroys any temporal signal.
    """
    rng = np.random.RandomState(seed)
    shuffled = {}
    for sym, sym_sigs in signals.items():
        shuffled_sigs = {}
        for k, (df, di, hold, atm) in sym_sigs.items():
            df_shuffled = df.copy()
            n = len(df_shuffled)
            # Shuffle the score column independently
            scores = df_shuffled['score'].values.copy()
            rng.shuffle(scores)
            df_shuffled['score'] = scores
            shuffled_sigs[k] = (df_shuffled, di, hold, atm)
        shuffled[sym] = shuffled_sigs
    return shuffled


def main():
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)
    
    all_symbols = set()
    for lst in PORTFOLIO.values():
        all_symbols.update(c[0] for c in lst)
    all_symbols = list(all_symbols)
    
    print(f"=== Monte Carlo Shuffle Audit ===")
    print(f"Symbols: {len(all_symbols)}")
    print(f"Shuffles: {N_SHUFFLES}")
    print(f"Real return (kelly_40_150): will be loaded from report")
    
    # Load real result
    report_path = os.path.join(os.path.dirname(__file__), '..', 'reports', 'phase5_walkforward', 'result.json')
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
        real_return = report['kelly_40_150']['return_pct']
        print(f"Real return: {real_return:+.2f}%")
    else:
        print("WARNING: No saved report found. Will run real simulation first.")
        real_return = None
    
    # Load data from ClickHouse
    print("\nLoading data from ClickHouse...")
    ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    data_all = {}
    for sym in all_symbols:
        q = f"""
            SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
                   o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell
            FROM moex.prices_5m p
            LEFT JOIN moex.prices_5m_oi o ON p.time=o.time AND p.symbol=o.symbol
            WHERE p.symbol='{sym}' AND p.time>='2024-01-01' AND p.time<='{TEST_END}'
            ORDER BY p.time
        """
        try:
            r = ch.query(q)
            if r.result_rows:
                cols = ['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell']
                df = pd.DataFrame(r.result_rows, columns=cols)
                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)
                data_all[sym] = df
                print(f"  ✓ {sym}: {len(df)} bars")
        except Exception as e:
            print(f"  ✗ {sym}: {e}")
    
    if not data_all:
        print("ERROR: No data loaded")
        sys.exit(1)
    
    # Precompute signals once (original)
    print("\nPrecomputing signals...")
    signals_original = precompute_signals(data_all, all_symbols)
    print(f"Signals computed for {len(signals_original)} tickers")
    
    # If no saved report, run real simulation now
    if real_return is None:
        print("\nRunning real simulation...")
        test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
        test_end_dt = datetime.strptime(TEST_END, '%Y-%m-%d') + timedelta(days=1)
        real_result = simulate_period(
            data_all, signals_original, test_start, test_end_dt,
            kelly_min=0.40, kelly_max=1.50,
            label="REAL (kelly 40-150%)"
        )
        real_return = real_result['return_pct']
        print(f"Real return: {real_return:+.2f}%")
    
    # Monte Carlo shuffles
    test_start = datetime.strptime('2025-01-01', '%Y-%m-%d')
    test_end_dt = datetime.strptime(TEST_END, '%Y-%m-%d') + timedelta(days=1)
    
    shuffled_returns = []
    
    for i in range(N_SHUFFLES):
        seed = RANDOM_SEED + i + 1000
        print(f"\n--- Shuffle {i+1}/{N_SHUFFLES} (seed={seed}) ---")
        
        # Shuffle
        signals_shuffled = shuffle_signals(signals_original, seed)
        
        # Run simulation
        try:
            result = simulate_period(
                data_all, signals_shuffled, test_start, test_end_dt,
                kelly_min=0.40, kelly_max=1.50,
                label=f"SHUFFLED #{i+1}"
            )
            ret = result['return_pct']
            shuffled_returns.append(ret)
            print(f"  → Return: {ret:+.2f}%")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            continue
    
    # Results
    print("\n" + "="*60)
    print("MONTE CARLO SHUFFLE RESULTS")
    print("="*60)
    
    if not shuffled_returns:
        print("ERROR: No successful shuffles")
        sys.exit(1)
    
    returns_arr = np.array(shuffled_returns)
    
    mean_shuffled = np.mean(returns_arr)
    std_shuffled = np.std(returns_arr, ddof=1)
    p95 = np.percentile(returns_arr, 95)
    p05 = np.percentile(returns_arr, 5)
    median_shuffled = np.percentile(returns_arr, 50)
    
    print(f"\nReal return:             {real_return:>+10.2f}%")
    print(f"Shuffled mean:           {mean_shuffled:>+10.2f}%")
    print(f"Shuffled median:         {median_shuffled:>+10.2f}%")
    print(f"Shuffled std:            {std_shuffled:>10.2f}%")
    print(f"Shuffled 5th percentile: {p05:>+10.2f}%")
    print(f"Shuffled 95th percentile:{p95:>+10.2f}%")
    print(f"Shuffled min:            {returns_arr.min():>+10.2f}%")
    print(f"Shuffled max:            {returns_arr.max():>+10.2f}%")
    
    # Визуальный вывод: гистограмма в ASCII
    print(f"\nDistribution of shuffled returns:")
    bins = 10
    hist, edges = np.histogram(returns_arr, bins=bins)
    max_h = max(hist)
    for j in range(bins):
        bar_len = int(hist[j] / max_h * 40) if max_h > 0 else 0
        marker = " *" if edges[j] <= real_return <= edges[j+1] else ""
        print(f"  [{edges[j]:>+8.2f}% - {edges[j+1]:>+8.2f}%] {'█'*bar_len} ({hist[j]}){marker}")
    
    real_pos = "WITHIN" if p05 <= real_return <= p95 else "OUTSIDE"
    
    if real_return > p95:
        conclusion = "✅ SIGNALS CARRY INFORMATION — real return exceeds 95th percentile of shuffled distribution"
    elif real_return < p05:
        conclusion = "⚠️ SIGNALS CARRY INFORMATION (negative edge) — real return below 5th percentile of shuffled distribution"
    else:
        conclusion = "❌ DATA SNOOPING — real return is within the shuffled distribution (95% CI)"
    
    print(f"\nConclusion: {conclusion}")
    print(f"Real return position relative to 90% CI: {real_pos}")
    
    # Save results
    output = {
        'n_shuffles': len(shuffled_returns),
        'real_return_pct': real_return,
        'shuffled_mean_pct': float(mean_shuffled),
        'shuffled_median_pct': float(median_shuffled),
        'shuffled_std_pct': float(std_shuffled),
        'shuffled_p05_pct': float(p05),
        'shuffled_p95_pct': float(p95),
        'shuffled_min_pct': float(returns_arr.min()),
        'shuffled_max_pct': float(returns_arr.max()),
        'all_shuffled_returns': [round(r, 4) for r in shuffled_returns],
        'conclusion': conclusion,
        'real_in_90ci': real_pos == "WITHIN"
    }
    
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'reports', 'phase5_walkforward')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'mc_shuffle_result.json')
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to: {out_path}")


if __name__ == '__main__':
    main()
