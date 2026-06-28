#!/usr/bin/env python3
"""Trailing TP vs Baseline test on MOEX futures Stop Hunt signals."""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
import json

CLICKHOUSE_URL = "http://10.0.0.60:8123"

def fetch_data(ticker):
    """Fetch 5-min OHLCV from ClickHouse."""
    today = date.today().strftime('%Y-%m-%d')
    query = f"""
    SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
           argMax(pr_open, SYSTIME) as opn,
           argMax(pr_high, SYSTIME) as hi,
           argMax(pr_low, SYSTIME) as lo,
           argMax(pr_close, SYSTIME) as prc,
           sum(vol) as vol
    FROM moex.tradestats_fo
    WHERE secid LIKE '{ticker}%' AND SYSTIME >= '2024-10-01' AND SYSTIME < '{today}'
    GROUP BY bt ORDER BY bt
    """
    r = requests.post(CLICKHOUSE_URL, data=query, timeout=120)
    r.raise_for_status()
    lines = r.text.strip().split('\n')
    rows = []
    for line in lines:
        parts = line.split('\t')
        if len(parts) >= 6:
            rows.append({
                'bt': parts[0],
                'opn': float(parts[1]),
                'hi': float(parts[2]),
                'lo': float(parts[3]),
                'prc': float(parts[4]),
                'vol': float(parts[5])
            })
    df = pd.DataFrame(rows)
    df['bt'] = pd.to_datetime(df['bt'])
    df = df.sort_values('bt').reset_index(drop=True)
    return df


def find_signals(df):
    """Find Stop Hunt signals:
    SHORT: fake_high = high[i] > max(high[i-20:i]) AND close[i] < high[i] - 0.3*(high[i]-low[i])
    LONG:  fake_low = low[i] < min(low[i-20:i]) AND close[i] > low[i] + 0.3*(high[i]-low[i])
    """
    n = len(df)
    signals = np.zeros(n, dtype=int)  # 0=none, 1=long, -1=short

    for i in range(20, n):
        hi20 = max(df['hi'].iloc[i-20:i])
        lo20 = min(df['lo'].iloc[i-20:i])

        # SHORT signal
        if df['hi'].iloc[i] > hi20 and df['prc'].iloc[i] < df['hi'].iloc[i] - 0.3 * (df['hi'].iloc[i] - df['lo'].iloc[i]):
            signals[i] = -1

        # LONG signal
        if df['lo'].iloc[i] < lo20 and df['prc'].iloc[i] > df['lo'].iloc[i] + 0.3 * (df['hi'].iloc[i] - df['lo'].iloc[i]):
            signals[i] = 1

    return signals


def run_baseline(df, signals):
    """Baseline: enter on signal, hold 12 bars, close at bar close."""
    trades = []
    n = len(df)
    i = 0
    while i < n:
        if signals[i] == 0:
            i += 1
            continue

        direction = signals[i]  # 1 = long, -1 = short
        entry_price = df['prc'].iloc[i]
        entry_time = df['bt'].iloc[i]
        exit_idx = min(i + 12, n - 1)

        if exit_idx <= i:
            i += 1
            continue

        # Track max drawdown intra-trade
        min_price = df['lo'].iloc[i]
        max_price = df['hi'].iloc[i]
        worst_pnl = 0.0  # worst return seen so far
        running_pnl = 0.0
        max_dd = 0.0
        for j in range(i + 1, exit_idx + 1):
            if direction == 1:
                run_pnl = (df['lo'].iloc[j] - entry_price) / entry_price * 100
                if run_pnl < worst_pnl:
                    worst_pnl = run_pnl
                # also check close
                c_pnl = (df['prc'].iloc[j] - entry_price) / entry_price * 100
                if c_pnl < worst_pnl:
                    worst_pnl = c_pnl
            else:
                run_pnl = (entry_price - df['hi'].iloc[j]) / entry_price * 100
                if run_pnl < worst_pnl:
                    worst_pnl = run_pnl
                c_pnl = (entry_price - df['prc'].iloc[j]) / entry_price * 100
                if c_pnl < worst_pnl:
                    worst_pnl = c_pnl

        exit_price = df['prc'].iloc[exit_idx]
        if direction == 1:
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        hold_bars = exit_idx - i

        trades.append({
            'entry_time': entry_time,
            'direction': direction,
            'entry_price': entry_price,
            'exit_time': df['bt'].iloc[exit_idx],
            'pnl_pct': pnl_pct,
            'max_dd_pct': abs(worst_pnl),
            'hold_bars': hold_bars,
            'won': pnl_pct > 0
        })

        # Advance past the entry bar to avoid re-entering the same signal
        i += 1

    return trades


def run_trailing_tp(df, signals, activation_pct=0.5, trail_pct=0.3, timeout_bars=96):
    """Trailing TP: enter on signal, trail with activation+trail stop."""
    trades = []
    n = len(df)
    i = 0
    while i < n:
        if signals[i] == 0:
            i += 1
            continue

        direction = signals[i]  # 1 = long, -1 = short
        entry_price = df['prc'].iloc[i]
        entry_time = df['bt'].iloc[i]

        trail_activated = False
        highest_fav = 0.0
        worst_dd = 0.0
        worst_price = entry_price

        exited = False
        for j in range(i + 1, min(i + timeout_bars + 1, n)):
            o, h, l, c = df['opn'].iloc[j], df['hi'].iloc[j], df['lo'].iloc[j], df['prc'].iloc[j]

            if direction == 1:
                # LONG
                fav_pct = (h - entry_price) / entry_price * 100
                if fav_pct > highest_fav:
                    highest_fav = fav_pct

                if not trail_activated:
                    if highest_fav >= activation_pct:
                        trail_activated = True

                if trail_activated:
                    trailing_stop = highest_fav - trail_pct
                    # Check intra-bar breach using low
                    stop_price = entry_price * (1 + trailing_stop / 100)
                    if l <= stop_price:
                        # Exit at stop price
                        exit_price = stop_price
                        exit_time = df['bt'].iloc[j]
                        pnl_pct = (exit_price - entry_price) / entry_price * 100
                        hold_bars = j - i
                        exited = True
                        break

                # Track drawdown
                run_pnl = (l - entry_price) / entry_price * 100
                if run_pnl < worst_dd:
                    worst_dd = run_pnl

            else:
                # SHORT
                fav_pct = (entry_price - l) / entry_price * 100
                if fav_pct > highest_fav:
                    highest_fav = fav_pct

                if not trail_activated:
                    if highest_fav >= activation_pct:
                        trail_activated = True

                if trail_activated:
                    trailing_stop = highest_fav - trail_pct
                    # Check intra-bar breach using high
                    stop_price = entry_price * (1 - trailing_stop / 100)
                    if h >= stop_price:
                        exit_price = stop_price
                        exit_time = df['bt'].iloc[j]
                        pnl_pct = (entry_price - exit_price) / entry_price * 100
                        hold_bars = j - i
                        exited = True
                        break

                # Track drawdown
                run_pnl = (entry_price - h) / entry_price * 100
                if run_pnl < worst_dd:
                    worst_dd = run_pnl

        if not exited:
            # Timeout or ran out of data - close at last bar close
            exit_idx = min(i + timeout_bars, n - 1)
            exit_price = df['prc'].iloc[exit_idx]
            exit_time = df['bt'].iloc[exit_idx]
            if direction == 1:
                pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * 100
            hold_bars = exit_idx - i

        trades.append({
            'entry_time': entry_time,
            'direction': direction,
            'entry_price': entry_price,
            'exit_time': exit_time,
            'pnl_pct': pnl_pct,
            'max_dd_pct': abs(worst_dd),
            'hold_bars': hold_bars,
            'won': pnl_pct > 0,
            'trail_activated': trail_activated if exited else False
        })

        # Advance past the entry bar to avoid re-entering the same signal
        i += 1

    return trades


def compute_netp80(pnl_list):
    """NetP80 = 80th percentile PnL - |20th percentile PnL|"""
    if len(pnl_list) == 0:
        return 0.0
    arr = np.array(pnl_list)
    p80 = np.percentile(arr, 80)
    p20 = np.percentile(arr, 20)
    return p80 - abs(p20)


def summarize(trades, label):
    """Print summary stats for a set of trades."""
    if len(trades) == 0:
        print(f"\n{'='*60}")
        print(f"{label}: NO TRADES")
        print(f"{'='*60}")
        return {
            'Method': label,
            'Trades': 0,
            'WR(%)': 0,
            'Mean Return(%)': 0,
            'Avg DD(%)': 0,
            'Avg Hold(bars)': 0,
            'NetP80': 0
        }

    pnls = [t['pnl_pct'] for t in trades]
    dds = [t['max_dd_pct'] for t in trades]
    holds = [t['hold_bars'] for t in trades]
    wins = sum(t['won'] for t in trades)

    wr = wins / len(trades) * 100
    mean_ret = np.mean(pnls)
    mean_dd = np.mean(dds)
    mean_hold = np.mean(holds)
    netp80 = compute_netp80(pnls)

    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    print(f"  Trades:           {len(trades)}")
    print(f"  Win Rate:         {wr:.2f}%")
    print(f"  Mean Return:      {mean_ret:.4f}%")
    print(f"  Avg Max DD:       {mean_dd:.4f}%")
    print(f"  Avg Hold (bars):  {mean_hold:.2f}")
    print(f"  NetP80:           {netp80:.4f}%")

    return {
        'Method': label,
        'Trades': len(trades),
        'WR(%)': round(wr, 2),
        'Mean Return(%)': round(mean_ret, 4),
        'Avg DD(%)': round(mean_dd, 4),
        'Avg Hold(bars)': round(mean_hold, 2),
        'NetP80': round(netp80, 4)
    }


def main():
    results = []

    for ticker in ['Si', 'GZ']:
        print(f"\n{'#'*60}")
        print(f"# Fetching data for {ticker}...")
        print(f"{'#'*60}")
        df = fetch_data(ticker)
        print(f"  Rows: {len(df)}")
        print(f"  Period: {df['bt'].min()} to {df['bt'].max()}")

        print(f"  Finding Stop Hunt signals...")
        signals = find_signals(df)
        n_signals = np.sum(signals != 0)
        n_long = np.sum(signals == 1)
        n_short = np.sum(signals == -1)
        print(f"  Signals: {n_signals} total ({n_long} long, {n_short} short)")

        print(f"  Running Baseline (hold 12 bars)...")
        baseline_trades = run_baseline(df, signals)
        bl_summary = summarize(baseline_trades, f"{ticker} - Baseline (12-bar hold)")

        print(f"  Running Trailing TP (activation=0.5%, trail=0.3%)...")
        trailing_trades = run_trailing_tp(df, signals, activation_pct=0.5, trail_pct=0.3)
        tp_summary = summarize(trailing_trades, f"{ticker} - Trailing TP")

        results.append(bl_summary)
        results.append(tp_summary)

    # Print comparison table
    print(f"\n\n{'='*80}")
    print(f"COMPARISON TABLE")
    print(f"{'='*80}")
    headers = ['Method', 'Trades', 'WR(%)', 'Mean Return(%)', 'Avg DD(%)', 'Avg Hold(bars)', 'NetP80']
    print(f"{'Method':<30} {'Trades':>8} {'WR(%)':>8} {'Mean Ret%':>10} {'Avg DD%':>10} {'Avg Hold':>10} {'NetP80':>10}")
    print(f"{'-'*30} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    for r in results:
        print(f"{r['Method']:<30} {r['Trades']:>8} {r['WR(%)']:>8.2f} {r['Mean Return(%)']:>10.4f} {r['Avg DD(%)']:>10.4f} {r['Avg Hold(bars)']:>10.2f} {r['NetP80']:>10.4f}")

    print(f"\n\nJSON output for parent:")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
