#!/usr/bin/env python3 -u
"""OB Redesign Cross-Validation Test — 3 variants (A, B, C) on all MOEX tickers."""
import psycopg2
import pandas as pd
import numpy as np
import os, sys, warnings, time
from collections import defaultdict
sys.stdout.reconfigure(line_buffering=True)  # unbuffered

warnings.filterwarnings('ignore')

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

TICKERS = ['AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu','EURRUBF','FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB','IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','MY','NA','NG','NM','NR','OJ','PD','PT','RB','RI','RL','RM','RN','SBERF','SE','SF','Si','SN','SP','SR','SS','SV','TN','TT','UC','USDRUBF','VB','VI','W4','X5','YD']

TF_CONFIG = {
    '5m':  {'rule': None,    'horizons': [3, 4, 6, 8]},
    '15m': {'rule': '15min', 'horizons': [2, 3, 4, 6]},
    '30m': {'rule': '30min', 'horizons': [2, 3, 4]},
    'H1':  {'rule': '1h',    'horizons': [2, 3, 4]},
}

BODY_MUL = 1.5
RANGE_MUL = 1.2
LOOKBACK = 20
MAX_RETEST_BARS = 30
RETEST_TOLERANCE = 0.001
MIN_SIGNALS = 50
MIN_BARS = 100
PF_CAP = 999.99

OUTPUT_DIR = '/home/user/projects/TQA-MOEX/docs/plans/ob_results'


def rolling_median(arr, w):
    """Fast rolling median using pandas."""
    s = pd.Series(arr)
    out = s.rolling(window=w, min_periods=1).median().shift(1)
    out[:1] = arr[0]  # first element
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


def detect_signals_variant_a(o, h, l, c, displ, horizon):
    n = len(c)
    signals = []
    for idx, direction in displ:
        ex = idx + horizon
        if ex >= n:
            continue
        entry = o[idx]
        exit_price = c[ex]
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        signals.append({'direction': direction, 'return_pct': ret})
    return signals


def detect_signals_variant_b(o, h, l, c, displ, horizon):
    n = len(c)
    signals = []
    for idx, direction in displ:
        ob_idx = idx - 1
        if direction == 'LONG':
            level = l[ob_idx]
            touch_cond = lambda j: l[j] <= level * (1 + RETEST_TOLERANCE)
        else:
            level = h[ob_idx]
            touch_cond = lambda j: h[j] >= level * (1 - RETEST_TOLERANCE)
        retest = None
        for j in range(idx + 1, min(idx + MAX_RETEST_BARS + 1, n)):
            if touch_cond(j):
                retest = j
                break
        if retest is None:
            continue
        ex = retest + horizon
        if ex >= n:
            continue
        entry = c[retest]
        exit_price = c[ex]
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        signals.append({'direction': direction, 'return_pct': ret})
    return signals


def detect_signals_variant_c(o, h, l, c, displ, horizon):
    n = len(c)
    signals = []
    for idx, direction in displ:
        ob_idx = idx - 1
        if direction == 'LONG':
            level = l[ob_idx]
            touch_cond = lambda j: l[j] <= level
        else:
            level = h[ob_idx]
            touch_cond = lambda j: h[j] >= level
        ex = idx + horizon
        if ex >= n:
            continue
        reached = False
        for j in range(idx, ex + 1):
            if touch_cond(j):
                reached = True
                break
        if not reached:
            continue
        entry = level
        exit_price = c[ex]
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        signals.append({'direction': direction, 'return_pct': ret})
    return signals


def compute_stats(signals):
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
    return {'n': n, 'wr': round(wr, 2), 'pf': round(pf, 4), 'avg_return': round(avg_ret, 4), 'max_dd': round(dd, 4)}


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
        n = len(c)
        bodies = np.abs(c - o)
        ranges = h - l
        med_b = rolling_median(bodies, LOOKBACK)
        med_r = rolling_median(ranges, LOOKBACK)
        displ = []
        for i in range(LOOKBACK + 1, n):
            if bodies[i] <= 0 or ranges[i] <= 0:
                continue
            if med_b[i] <= 0 or med_r[i] <= 0:
                continue
            if bodies[i] > med_b[i] * BODY_MUL and ranges[i] > med_r[i] * RANGE_MUL:
                direction = 'LONG' if c[i] > o[i] else 'SHORT'
                displ.append((i, direction))
        if not displ:
            continue
        for var_name, var_fn in [
            ('A', detect_signals_variant_a),
            ('B', detect_signals_variant_b),
            ('C', detect_signals_variant_c),
        ]:
            for horizon in tf_cfg['horizons']:
                sigs = var_fn(o, h, l, c, displ, horizon)
                stats = compute_stats(sigs)
                if stats:
                    results.append({'ticker': symbol, 'tf': tf_name, 'variant': var_name,
                                    'horizon': horizon, **stats})
    return results if results else None


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    t0 = time.time()
    print(f"Connecting to DB...")
    conn = psycopg2.connect(**DB)
    print("Loading all ticker data...")
    by_symbol = load_all_data(conn)
    conn.close()
    print(f"Loaded {len(by_symbol)} tickers. Processing...")

    all_results = []
    for idx, symbol in enumerate(TICKERS, 1):
        rows = by_symbol.get(symbol, [])
        if len(rows) < MIN_BARS:
            print(f"[{idx:2d}/{len(TICKERS)}] {symbol:8s} -> SKIP ({len(rows)} bars)")
            continue
        res = process_ticker(symbol, rows)
        if res:
            all_results.extend(res)
            n_combos = len(res)
            best_wr = max(r['wr'] for r in res)
            print(f"[{idx:2d}/{len(TICKERS)}] {symbol:8s} -> {n_combos:2d} combos, best WR {best_wr:5.1f}%")
        else:
            print(f"[{idx:2d}/{len(TICKERS)}] {symbol:8s} -> 0 combos")

    if not all_results:
        print("No results!")
        sys.exit(1)

    df = pd.DataFrame(all_results)
    cols = ['ticker', 'tf', 'variant', 'horizon', 'n', 'wr', 'pf', 'avg_return', 'max_dd']
    df = df[cols].sort_values('wr', ascending=False).reset_index(drop=True)

    df.to_csv(os.path.join(OUTPUT_DIR, 'leaderboard.csv'), index=False)
    for var in ('A', 'B', 'C'):
        sub = df[df['variant'] == var].head(20)
        sub.to_csv(os.path.join(OUTPUT_DIR, f'by_variant_{var}.csv'), index=False)
    best_ticker = df.loc[df.groupby('ticker')['wr'].idxmax()]
    best_ticker.to_csv(os.path.join(OUTPUT_DIR, 'best_per_ticker.csv'), index=False)
    best_tf = df.loc[df.groupby('tf')['wr'].idxmax()]
    best_tf.to_csv(os.path.join(OUTPUT_DIR, 'best_per_tf.csv'), index=False)

    print("\n" + "=" * 100)
    print("LEADERBOARD — TOP-10 by WR")
    print("=" * 100)
    hdr = f"{'#':4s} {'Ticker':8s} {'TF':6s} {'Var':4s} {'H':4s} {'N':>6s} {'WR%':>7s} {'PF':>8s} {'AvgRet%':>9s} {'MaxDD%':>8s}"
    print(hdr)
    print("-" * 75)
    for i, (_, row) in enumerate(df.head(10).iterrows(), 1):
        print(f"{i:4d} {row['ticker']:8s} {row['tf']:6s} {row['variant']:4s} {int(row['horizon']):4d} {int(row['n']):6d} {row['wr']:7.2f} {row['pf']:8.2f} {row['avg_return']:9.4f} {row['max_dd']:8.2f}")
    print("-" * 75)
    elapsed = time.time() - t0
    print(f"\nTotal combos: {len(df)} | Unique tickers: {df['ticker'].nunique()} | Time: {elapsed/60:.1f} min")
    print(f"Results saved to {OUTPUT_DIR}/")


if __name__ == '__main__':
    main()
