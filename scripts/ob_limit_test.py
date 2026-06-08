#!/usr/bin/env python3 -u
"""OB Limit Order Retest — Variants D (limit at OB level) and E (limit at close)."""
import psycopg2
import pandas as pd
import numpy as np
import os, sys, warnings, time
from collections import defaultdict

sys.stdout.reconfigure(line_buffering=True)
warnings.filterwarnings('ignore')

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

# 54 tickers (exclude forex pairs)
TICKERS = ['AF','AL','BM','BR','CC','CE','CR','DX','ED','FF','GAZPF','GD','GK','GL','GZ','HS','HY','IB','IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','NA','NG','NM','NR','OJ','PD','PT','RB','RI','RL','RM','RN','SBERF','SE','SF','Si','SN','SP','SR','SS','SV','TN','TT','UC','VB','VI','W4','X5','YD']

TF_CONFIG = {
    'H1': {'rule': '1h', 'horizons': [2, 3, 4]},
}

BODY_MUL = 1.5
RANGE_MUL = 1.2
LOOKBACK = 20
LIMIT_LOOKBACK = 5  # max bars to wait for limit fill
MIN_SIGNALS = 50
MIN_BARS = 100
PF_CAP = 999.99

OUTPUT_DIR = '/home/user/projects/TQA-MOEX/docs/plans/ob_results'


def rolling_median(arr, w):
    s = pd.Series(arr)
    out = s.rolling(window=w, min_periods=1).median().shift(1)
    out[:1] = arr[0]
    return out.ffill().fillna(arr[0]).values


def load_all_data(conn):
    cur = conn.cursor()
    placeholders = ','.join(['%s'] * len(TICKERS))
    cur.execute(f"""
        SELECT symbol, time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol IN ({placeholders})
        ORDER BY symbol, time
    """, TICKERS)
    rows = cur.fetchall()
    cur.close()
    by_symbol = defaultdict(list)
    for r in rows:
        by_symbol[r[0]].append(r)
    return by_symbol


def resample(rows, rule):
    df = pd.DataFrame(rows, columns=['symbol', 'time', 'open', 'high', 'low', 'close', 'volume'])
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    if rule is None:
        return df
    resampled = df.resample(rule).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
    })
    resampled.dropna(inplace=True)
    return resampled


def detect_displacements(o, h, l, c):
    """Find all displacement bars."""
    n = len(c)
    bodies = np.abs(c - o)
    ranges = h - l
    med_b = rolling_median(bodies, LOOKBACK)
    med_r = rolling_median(ranges, LOOKBACK)

    displ = []
    for i in range(LOOKBACK + 1, n):
        if bodies[i] <= 0 or ranges[i] <= 0 or med_b[i] <= 0 or med_r[i] <= 0:
            continue
        if bodies[i] > med_b[i] * BODY_MUL and ranges[i] > med_r[i] * RANGE_MUL:
            direction = 'LONG' if c[i] > o[i] else 'SHORT'
            displ.append({'idx': i, 'direction': direction, 'ob_idx': i-1})
    return displ, bodies, ranges, med_b, med_r


def detect_variant_d(o, h, l, c, displ, horizon):
    """Limit at OB level (low[ob_idx] for LONG, high[ob_idx] for SHORT)."""
    n = len(c)
    signals = []
    for d in displ:
        i = d['idx']
        direction = d['direction']
        ob_idx = d['ob_idx']

        if direction == 'LONG':
            level = l[ob_idx]
        else:
            level = h[ob_idx]

        # Look for fill within LIMIT_LOOKBACK bars after displacement
        fill_bar = None
        for j in range(i, min(i + LIMIT_LOOKBACK, n)):
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
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        signals.append({'direction': direction, 'return_pct': ret, 'fill_bar': fill_bar, 'fill_time': i})
    return signals


def detect_variant_e(o, h, l, c, displ, horizon):
    """Limit at close of displacement bar."""
    n = len(c)
    signals = []
    for d in displ:
        i = d['idx']
        direction = d['direction']
        level = c[i]

        # Look for fill within LIMIT_LOOKBACK bars
        fill_bar = None
        for j in range(i, min(i + LIMIT_LOOKBACK, n)):
            if direction == 'LONG' and c[j] >= level:
                fill_bar = j
                break
            elif direction == 'SHORT' and c[j] <= level:
                fill_bar = j
                break

        if fill_bar is None:
            continue

        ex = fill_bar + horizon
        if ex >= n:
            continue

        entry = level
        exit_price = c[ex]
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        signals.append({'direction': direction, 'return_pct': ret, 'fill_bar': fill_bar, 'fill_time': i})
    return signals


def compute_stats(signals, total_displ):
    """Return stats dict or None if < MIN_SIGNALS."""
    if len(signals) < MIN_SIGNALS:
        return None
    rets = np.array([s['return_pct'] for s in signals])
    n = len(rets)
    wr = float(np.mean(rets > 0) * 100)
    gains = np.sum(rets[rets > 0])
    losses = np.abs(np.sum(rets[rets < 0]))
    pf = min(gains / losses, PF_CAP) if losses > 0 else PF_CAP
    avg_ret = float(np.mean(rets))
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    dd = float(np.max(peak - cum))
    fill_rate = (n / total_displ * 100) if total_displ > 0 else 0
    return {
        'n': n, 'wr': round(wr, 2), 'pf': round(pf, 4),
        'avg_return': round(avg_ret, 4), 'max_dd': round(dd, 4),
        'fill_rate': round(fill_rate, 1),
    }


def process_ticker(symbol, rows):
    if len(rows) < MIN_BARS:
        return None
    results = []
    for tf_name, tf_cfg in TF_CONFIG.items():
        df = resample(rows, tf_cfg['rule'])
        if len(df) < MIN_BARS:
            continue
        o = df['open'].values.astype(float)
        h = df['high'].values.astype(float)
        l = df['low'].values.astype(float)
        c = df['close'].values.astype(float)

        displ, _, _, _, _ = detect_displacements(o, h, l, c)
        if not displ:
            continue
        total_displ = len(displ)

        for var_name, var_fn in [('D', detect_variant_d), ('E', detect_variant_e)]:
            for horizon in tf_cfg['horizons']:
                sigs = var_fn(o, h, l, c, displ, horizon)
                stats = compute_stats(sigs, total_displ)
                if stats:
                    results.append({
                        'ticker': symbol, 'tf': tf_name, 'variant': var_name,
                        'horizon': horizon, **stats
                    })
    return results if results else None


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    t0 = time.time()
    print(f"Connecting to DB...", flush=True)
    conn = psycopg2.connect(**DB)
    print("Loading all ticker data...", flush=True)
    by_symbol = load_all_data(conn)
    conn.close()
    print(f"Loaded {len(by_symbol)} tickers. Processing...", flush=True)

    all_results = []
    for idx, symbol in enumerate(TICKERS, 1):
        rows = by_symbol.get(symbol, [])
        if len(rows) < MIN_BARS:
            print(f"[{idx:2d}/{len(TICKERS)}] {symbol:8s} -> SKIP ({len(rows)} bars)", flush=True)
            continue
        res = process_ticker(symbol, rows)
        if res:
            all_results.extend(res)
            n_combos = len(res)
            best_wr = max(r['wr'] for r in res)
            print(f"[{idx:2d}/{len(TICKERS)}] {symbol:8s} -> {n_combos:2d} combos, best WR {best_wr:5.1f}%", flush=True)
        else:
            print(f"[{idx:2d}/{len(TICKERS)}] {symbol:8s} -> 0 combos", flush=True)

    if not all_results:
        print("No results!", flush=True)
        sys.exit(1)

    df = pd.DataFrame(all_results)
    cols = ['ticker', 'tf', 'variant', 'horizon', 'n', 'wr', 'pf', 'avg_return', 'max_dd', 'fill_rate']
    df = df[cols].sort_values('wr', ascending=False).reset_index(drop=True)

    df.to_csv(os.path.join(OUTPUT_DIR, 'leaderboard_limit.csv'), index=False)
    for var in ('D', 'E'):
        sub = df[df['variant'] == var].head(20)
        sub.to_csv(os.path.join(OUTPUT_DIR, f'by_variant_{var}.csv'), index=False)
    best_ticker = df.loc[df.groupby('ticker')['wr'].idxmax()]
    best_ticker.to_csv(os.path.join(OUTPUT_DIR, 'best_per_ticker_limit.csv'), index=False)

    print("\n" + "=" * 100, flush=True)
    print("LEADERBOARD — TOP-20 (Limit Orders)", flush=True)
    print("=" * 100, flush=True)
    hdr = f"{'#':4s} {'Ticker':8s} {'Var':4s} {'H':4s} {'N':>6s} {'WR%':>7s} {'PF':>8s} {'Fill%':>7s} {'AvgRet%':>9s}"
    print(hdr, flush=True)
    print("-" * 70, flush=True)
    for i, (_, row) in enumerate(df.head(20).iterrows(), 1):
        print(f"{i:4d} {row['ticker']:8s} {row['variant']:4s} {int(row['horizon']):4d} {int(row['n']):6d} {row['wr']:7.2f} {row['pf']:8.2f} {row['fill_rate']:7.1f} {row['avg_return']:9.4f}", flush=True)
    print("-" * 70, flush=True)

    for v in ('D', 'E'):
        sub = df[df['variant'] == v]
        avg_wr = sub['wr'].mean()
        avg_pf = sub['pf'].mean()
        avg_fill = sub['fill_rate'].mean()
        total_n = sub['n'].sum()
        print(f"\nVariant {v}: {len(sub)} combos, {sub['ticker'].nunique()} tickers, avg WR={avg_wr:.1f}% PF={avg_pf:.2f} fill={avg_fill:.1f}% total_signals={total_n}", flush=True)

    elapsed = time.time() - t0
    print(f"\nTotal combos: {len(df)} | Unique tickers: {df['ticker'].nunique()} | Time: {elapsed/60:.1f} min", flush=True)
    print(f"Results saved to {OUTPUT_DIR}/", flush=True)


if __name__ == '__main__':
    main()
