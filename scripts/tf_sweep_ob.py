#!/usr/bin/env python3 -u
"""Per-ticker TF sweep for Order Block (Variant D) — H1, H2, H4."""
import os, sys, warnings, time
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings('ignore')

from trading_bot.ob_engine import load_price_data, _rolling_median
from trading_bot import DEFAULT_OB_CONFIG, OB_TICKERS

PF_CAP = 999.99
OUTPUT_DIR = '/home/user/projects/TQA-MOEX/docs/plans/tf_sweep_results'

TF_RULES = {
    'H1': '1h',
    'H2': '2h',
    'H4': '4h',
}

def resample_to_tf(rows: list, rule: str) -> pd.DataFrame:
    """Resample 5m tuples to target TF DataFrame."""
    df = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    resampled = df.resample(rule).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
    })
    resampled.dropna(inplace=True)
    return resampled


def detect_ob_signals_tf(df: pd.DataFrame, config: dict) -> list:
    """
    Detect Order Block signals on a pre-resampled DataFrame.
    Same logic as detect_order_block_signals, but skips internal resample_h1.
    """
    body_mul = config.get('body_mul', 1.5)
    range_mul = config.get('range_mul', 1.2)
    lookback = config.get('lookback', 20)
    horizon = config.get('horizon', 2)
    limit_lookback = config.get('limit_lookback', 5)
    max_signal_age = config.get('max_signal_age', 6)  # not used in backtest

    n = len(df)
    if n < 100:
        return []

    o = df['open'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)
    c = df['close'].values.astype(float)
    times = df.index

    bodies = np.abs(c - o)
    ranges = h - l
    med_body = _rolling_median(bodies, lookback)
    med_range = _rolling_median(ranges, lookback)

    displ = []
    for i in range(lookback + 1, n):
        if bodies[i] <= 0 or ranges[i] <= 0 or med_body[i] <= 0 or med_range[i] <= 0:
            continue
        if bodies[i] > med_body[i] * body_mul and ranges[i] > med_range[i] * range_mul:
            direction = 'LONG' if c[i] > o[i] else 'SHORT'
            displ.append({'idx': i, 'direction': direction, 'ob_idx': i - 1})

    signals = []
    for d in displ:
        i = d['idx']
        direction = d['direction']
        ob_idx = d['ob_idx']
        level = l[ob_idx] if direction == 'LONG' else h[ob_idx]

        fill_bar = None
        for j in range(i, min(i + limit_lookback, n)):
            if direction == 'LONG' and l[j] <= level:
                fill_bar = j
                break
            elif direction == 'SHORT' and h[j] >= level:
                fill_bar = j
                break
        if fill_bar is None:
            continue

        ex = fill_bar + horizon
        if ex >= n:
            continue

        entry = level
        exit_price = c[ex]
        if direction == 'LONG':
            return_pct = (exit_price - entry) / entry * 100.0
        else:
            return_pct = (entry - exit_price) / entry * 100.0

        signals.append({
            'ticker': '?',
            'direction': direction,
            'entry': round(entry, 4),
            'exit': round(exit_price, 4),
            'return_pct': round(return_pct, 4),
        })
    return signals


def compute_stats(signals):
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'avg_return': 0.0, 'max_dd': 0.0}
    returns = np.array([s['return_pct'] for s in signals])
    n = len(returns)
    if n < 30:
        return {'n': n, 'wr': 0.0, 'pf': 0.0, 'avg_return': 0.0, 'max_dd': 0.0}
    wr = float(np.mean(returns > 0) * 100)
    gains = np.sum(returns[returns > 0])
    losses = np.abs(np.sum(returns[returns < 0]))
    pf = min(gains / losses, PF_CAP) if losses > 0 else PF_CAP
    avg_return = float(np.mean(returns))
    cum = np.cumsum(returns)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum)) if len(cum) > 0 else 0.0
    return {'n': n, 'wr': round(wr, 2), 'pf': round(pf, 4), 'avg_return': round(avg_return, 4), 'max_dd': round(max_dd, 4)}


def test_ob():
    results = []
    for sym, cfg in OB_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        base_cfg = {**DEFAULT_OB_CONFIG, **cfg}
        rows = load_price_data(sym, days=730)
        if not rows or len(rows) < 100:
            print(f"  SKIP {sym:8s}: no data")
            continue

        for tf_name, tf_rule in TF_RULES.items():
            df = resample_to_tf(rows, tf_rule)
            if len(df) < 100:
                print(f"  SKIP {sym:8s} {tf_name:4s}: only {len(df)} bars")
                continue

            sigs = detect_ob_signals_tf(df, base_cfg)
            st = compute_stats(sigs)
            if st['n'] >= 30:
                results.append({'strategy': 'OrderBlock', 'ticker': sym, 'tf': tf_name,
                                'horizon': base_cfg['horizon'], **st})
                print(f"  OB  {sym:8s} {tf_name:4s} h={base_cfg['horizon']:2d}: n={st['n']:5d} WR={st['wr']:5.1f}% PF={st['pf']:.2f}")
            else:
                print(f"  OB  {sym:8s} {tf_name:4s}: only {st['n']} signals (<30)")
    return results


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    t0 = time.time()
    print("=" * 60)
    print("Order Block TF sweep — H1, H2, H4")
    print("=" * 60)
    all_results = test_ob()

    if not all_results:
        print("No results!")
        sys.exit(1)

    df = pd.DataFrame(all_results)
    csv_path = os.path.join(OUTPUT_DIR, 'tf_sweep_ob.csv')
    df.to_csv(csv_path, index=False)

    print("\n" + "=" * 80)
    print("BEST TF PER TICKER")
    print("=" * 80)
    best = df.loc[df.groupby('ticker')['wr'].idxmax()]

    # Add column: delta vs H1
    h1_stats = df[df['tf'] == 'H1'][['ticker', 'wr', 'n']].rename(columns={'wr': 'wr_h1', 'n': 'n_h1'})
    best = best.merge(h1_stats, on='ticker', how='left')
    best['delta'] = best['wr'] - best['wr_h1']

    hdr = f"{'Ticker':8s} {'Best TF':6s} {'N':>6s} {'WR%':>7s} {'WR_H1':>7s} {'Δ':>7s} {'PF':>8s} {'MaxDD':>8s}"
    print(hdr)
    print("-" * 65)
    changes = 0
    for _, row in best.sort_values('wr', ascending=False).iterrows():
        delta_str = f"{row['delta']:+.1f}%" if pd.notna(row['delta']) else "  N/A"
        marker = " 🔥" if pd.notna(row['delta']) and row['delta'] > 2 else ""
        if pd.notna(row['delta']) and row['delta'] > 2:
            changes += 1
        print(f"{row['ticker']:8s} {row['tf']:6s} {int(row['n']):6d} {row['wr']:7.2f} {row['wr_h1'] if pd.notna(row.get('wr_h1')) else '':>7} {delta_str:>7} {row['pf']:8.2f} {row['max_dd']:8.4f}{marker}")

    print(f"\nТикеров с улучшением >2%: {changes} / {len(best)}")
    print(f"Time: {time.time()-t0:.1f}s")
    print(f"Saved to {csv_path}")

    return best


if __name__ == '__main__':
    main()
