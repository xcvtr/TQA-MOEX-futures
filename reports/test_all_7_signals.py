#!/usr/bin/env python3
"""
Comprehensive 7-signal test on MOEX futures (Si, GZ, CR).
Each signal → baseline (hold 12 bars) vs trailing TP (activation=0.5%, trail=0.3%, max_bars=96).
Metrics: WR, Mean Return%, Avg DD%, Avg Hold Bars, NetP80.
Data: moex.tradestats_fo via ClickHouse 10.0.0.60:8123, 2024-10-01 to today.
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, date
import json
import sys

CLICKHOUSE_URL = "http://10.0.0.60:8123"
START_DATE = "2024-10-01"
END_DATE = date.today().isoformat()
TICKERS = ["Si", "GZ", "CR"]

# ─── Data fetching ───────────────────────────────────────────────

def fetch_5m_data(ticker):
    """Fetch 5-min OHLCV + OI + vol_b/vol_s + disb from ClickHouse."""
    query = f"""
    SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE, 'Europe/Moscow') as bt,
           argMax(pr_open, SYSTIME) as opn,
           argMax(pr_high, SYSTIME) as hi,
           argMax(pr_low, SYSTIME) as lo,
           argMax(pr_close, SYSTIME) as prc,
           sum(vol) as vol,
           argMax(oi_close, SYSTIME) as oi,
           sum(vol_b) as vol_b,
           sum(vol_s) as vol_s,
           sum(disb) as disb
    FROM moex.tradestats_fo
    WHERE secid LIKE '{ticker}%'
      AND SYSTIME >= '{START_DATE}'
      AND SYSTIME < '{END_DATE}'
    GROUP BY bt
    ORDER BY bt
    """
    r = requests.post(CLICKHOUSE_URL, data=query, timeout=180)
    r.raise_for_status()
    lines = r.text.strip().split('\n')
    rows = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 10:
            try:
                rows.append({
                    'bt': parts[0],
                    'opn': float(parts[1]),
                    'hi': float(parts[2]),
                    'lo': float(parts[3]),
                    'prc': float(parts[4]),
                    'vol': float(parts[5]),
                    'oi': float(parts[6]) if parts[6] not in ('', '\\N') else np.nan,
                    'vol_b': float(parts[7]) if parts[7] not in ('', '\\N') else 0,
                    'vol_s': float(parts[8]) if parts[8] not in ('', '\\N') else 0,
                    'disb': float(parts[9]) if parts[9] not in ('', '\\N') else 0,
                })
            except (ValueError, IndexError):
                continue
    df = pd.DataFrame(rows)
    if len(df) == 0:
        return df
    df['bt'] = pd.to_datetime(df['bt'])
    df = df.sort_values('bt').reset_index(drop=True)

    # Derived columns
    df['hour'] = df['bt'].dt.hour
    df['minute'] = df['bt'].dt.minute
    df['date'] = df['bt'].dt.date

    # CVD
    df['cvd'] = df['vol_b'] - df['vol_s']

    # Filter extreme OI jumps (contract roll)
    oi_safe = df['oi'].replace(0, np.nan).ffill()
    oi_chg = oi_safe.pct_change().abs()
    df['oi'] = oi_safe

    return df


# ─── Signal detectors ───────────────────────────────────────────

def signal_stop_hunt(df):
    """
    Signal 1: Stop Hunt.
    SHORT: hi[i] > max(hi[i-20:i]) AND close[i] < hi[i] - 0.3*(hi[i]-lo[i])
    LONG:  lo[i] < min(lo[i-20:i]) AND close[i] > lo[i] + 0.3*(hi[i]-lo[i])
    Returns array: 0=no, 1=long, -1=short
    """
    n = len(df)
    sig = np.zeros(n, dtype=int)
    for i in range(20, n):
        hi20 = df['hi'].iloc[i-20:i].max()
        lo20 = df['lo'].iloc[i-20:i].min()
        candle_range = df['hi'].iloc[i] - df['lo'].iloc[i]
        # SHORT
        if df['hi'].iloc[i] > hi20 and df['prc'].iloc[i] < df['hi'].iloc[i] - 0.3 * candle_range:
            sig[i] = -1
        # LONG
        if df['lo'].iloc[i] < lo20 and df['prc'].iloc[i] > df['lo'].iloc[i] + 0.3 * candle_range:
            sig[i] = 1
    return sig


def signal_oi_spike_new_shorts(df):
    """
    Signal 2: OI Spike new_shorts (SHORT only).
    OI z-score > 2.0 over 30 bars AND price down on the bar → new short entries.
    """
    n = len(df)
    sig = np.zeros(n, dtype=int)
    oi = df['oi'].values.astype(float)
    oi_log = np.where(oi > 0, np.log(np.maximum(oi, 1)), 0)
    oi_z = np.zeros(n)
    for i in range(30, n):
        window = oi_log[i-30:i]
        mu = window.mean()
        std = window.std()
        if std > 0:
            oi_z[i] = (oi_log[i] - mu) / std
    prc = df['prc'].values
    for i in range(30, n):
        if oi_z[i] > 2.0 and prc[i] < prc[i-1]:
            sig[i] = -1  # SHORT only
    return sig


def signal_disb_oi_combo(df):
    """
    Signal 3: Disb_z + OI_z Combo.
    disb_z > 1.0 AND oi_z > 1.5 → LONG (buying + new longs)
    disb_z < -1.0 AND oi_z > 1.5 → SHORT (selling + new shorts)
    """
    n = len(df)
    sig = np.zeros(n, dtype=int)

    # Disb z-score
    disb = df['disb'].values.astype(float)
    disb_z = np.zeros(n)
    for i in range(30, n):
        window = disb[i-30:i]
        mu = window.mean()
        std = window.std()
        if std > 0:
            disb_z[i] = (disb[i] - mu) / std

    # OI z-score
    oi = df['oi'].values.astype(float)
    oi_log = np.where(oi > 0, np.log(np.maximum(oi, 1)), 0)
    oi_z = np.zeros(n)
    for i in range(30, n):
        window = oi_log[i-30:i]
        mu = window.mean()
        std = window.std()
        if std > 0:
            oi_z[i] = (oi_log[i] - mu) / std

    for i in range(30, n):
        if disb_z[i] > 1.0 and oi_z[i] > 1.5:
            sig[i] = 1  # LONG
        elif disb_z[i] < -1.0 and oi_z[i] > 1.5:
            sig[i] = -1  # SHORT
    return sig


def signal_lunch_reversal(df):
    """
    Signal 4: Lunch Reversal (session 13-14 MSK).
    Enter at 13:00 bar.
    If price grew from 10:00 to 13:00 → SHORT (mean reversion).
    If price fell from 10:00 to 13:00 → LONG.
    """
    n = len(df)
    sig = np.zeros(n, dtype=int)

    for i in range(n):
        # Signal at 13:00 MSK
        if df['hour'].iloc[i] != 13 or df['minute'].iloc[i] != 0:
            continue

        # Find 10:00 bar today
        today = df['date'].iloc[i]
        morning_mask = (df['date'] == today) & (df['hour'] == 10) & (df['minute'] == 0)
        morning_idx = morning_mask.idxmax() if morning_mask.any() else None
        if morning_idx is None:
            continue

        morning_price = df['prc'].iloc[morning_idx]
        entry_price = df['prc'].iloc[i]
        pct_chg = (entry_price / morning_price - 1) * 100

        if pct_chg > 0.1:  # price grew → SHORT
            sig[i] = -1
        elif pct_chg < -0.1:  # price fell → LONG
            sig[i] = 1

    return sig


def signal_churn(df):
    """
    Signal 5: Churn (OI flat + volume surge).
    OI flat over 5 bars (|oi - oi.shift(5)|/oi.shift(5) < 0.01)
    AND vol/rolling(20).mean() > 2.0
    Trend via SMA(10): trend up → SHORT, trend down → LONG.
    """
    n = len(df)
    sig = np.zeros(n, dtype=int)

    if n < 25:
        return sig

    oi = df['oi'].values.astype(float)
    vol = df['vol'].values.astype(float)
    prc = df['prc'].values.astype(float)

    # OI flat
    oi_prev5 = np.roll(oi, 5)
    oi_prev5[:5] = np.nan
    oi_chg_pct = np.where(oi_prev5 > 0, np.abs(oi - oi_prev5) / oi_prev5, 999)
    oi_flat = oi_chg_pct < 0.01

    # Volume surge
    vol_ma = pd.Series(vol).rolling(20, min_periods=10).mean().values
    vol_ratio = np.where(vol_ma > 0, vol / vol_ma, 0)
    vol_surge = vol_ratio > 2.0

    # Combined signal
    raw_sig = oi_flat & vol_surge

    # Trend via SMA(10)
    sma10 = pd.Series(prc).rolling(10).mean().values
    sma10_shift3 = np.roll(sma10, 3)
    sma10_shift3[:3] = np.nan
    trend_up = sma10 > sma10_shift3

    for i in range(25, n):
        if raw_sig[i]:
            if trend_up[i]:
                sig[i] = -1  # SHORT
            else:
                sig[i] = 1   # LONG
    return sig


def signal_volume_profile_short(df):
    """
    Signal 6: Volume Profile short-only.
    Daily VWAP-based: price > VWAP AND vol > 2× avg(20-bar vol) → SHORT.
    Idea: high volume near resistance → distribution.
    """
    n = len(df)
    sig = np.zeros(n, dtype=int)

    if n < 20:
        return sig

    vol = df['vol'].values.astype(float)
    prc = df['prc'].values.astype(float)
    vol_ma = pd.Series(vol).rolling(20, min_periods=10).mean().values

    # Compute daily VWAP
    df['vwap'] = df['vol'] * df['prc']
    daily_vwap = df.groupby('date').apply(
        lambda g: pd.Series((g['vol'] * g['prc']).cumsum() / g['vol'].cumsum().replace(0, np.nan).ffill().values, index=g.index)
    )
    # daily_vwap is a Series with same index
    if isinstance(daily_vwap, pd.Series):
        for idx in daily_vwap.index:
            i = idx if isinstance(idx, int) else df.index.get_loc(idx) if idx in df.index else -1
            if i >= 0:
                vwap_val = daily_vwap.loc[idx] if len(df.columns) == 0 else daily_vwap[idx]
    # Simpler approach: compute rolling VWAP per day
    df['vol_prc'] = df['vol'] * df['prc']
    df['cum_vol'] = df.groupby('date')['vol'].cumsum()
    df['cum_vol_prc'] = df.groupby('date')['vol_prc'].cumsum()
    df['vwap'] = df['cum_vol_prc'] / df['cum_vol'].replace(0, np.nan)

    for i in range(20, n):
        if vol_ma[i] <= 0:
            continue
        vol_ratio = vol[i] / vol_ma[i]
        if prc[i] > df['vwap'].iloc[i] and vol_ratio > 2.0:
            sig[i] = -1  # SHORT only
    return sig


def signal_cvd(df):
    """
    Signal 7: CVD (dcvd_z > 0.6).
    dcvd = change in cumulative volume delta.
    dcvd_z > 0.6 → aggressive buying → LONG.
    Enter at signal bar, hold.
    """
    n = len(df)
    sig = np.zeros(n, dtype=int)

    if n < 30:
        return sig

    cvd = df['cvd'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0])

    dcvd_ma = pd.Series(dcvd).rolling(20).mean().values
    dcvd_std = pd.Series(dcvd).rolling(20).std().values
    dcvd_z = np.where(dcvd_std > 0, (dcvd - dcvd_ma) / dcvd_std, 0)

    for i in range(20, n):
        if dcvd_z[i] > 0.6:
            sig[i] = 1  # LONG (buying pressure)
        # Also short when dcvd_z < -0.6 (selling pressure)
        elif dcvd_z[i] < -0.6:
            sig[i] = -1  # SHORT

    return sig


# ─── Signal registry ────────────────────────────────────────────

SIGNALS = [
    ("Stop Hunt", signal_stop_hunt),
    ("OI Spike new_shorts", signal_oi_spike_new_shorts),
    ("Disb_z+OI_z Combo", signal_disb_oi_combo),
    ("Lunch Reversal", signal_lunch_reversal),
    ("Churn", signal_churn),
    ("Volume Profile short-only", signal_volume_profile_short),
    ("CVD (dcvd_z>0.6)", signal_cvd),
]


# ─── Testing methods ────────────────────────────────────────────

def run_baseline(df, signals, hold_bars=12):
    """Baseline: enter on signal, hold N bars, close at bar close."""
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
        exit_idx = min(i + hold_bars, n - 1)

        if exit_idx <= i:
            i += 1
            continue

        # Track max drawdown intra-trade
        worst_pnl = 0.0
        for j in range(i + 1, exit_idx + 1):
            if direction == 1:
                run_pnl = (df['lo'].iloc[j] - entry_price) / entry_price * 100
                c_pnl = (df['prc'].iloc[j] - entry_price) / entry_price * 100
                if run_pnl < worst_pnl:
                    worst_pnl = run_pnl
                if c_pnl < worst_pnl:
                    worst_pnl = c_pnl
            else:
                run_pnl = (entry_price - df['hi'].iloc[j]) / entry_price * 100
                c_pnl = (entry_price - df['prc'].iloc[j]) / entry_price * 100
                if run_pnl < worst_pnl:
                    worst_pnl = run_pnl
                if c_pnl < worst_pnl:
                    worst_pnl = c_pnl

        exit_price = df['prc'].iloc[exit_idx]
        if direction == 1:
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        hold = exit_idx - i

        trades.append({
            'entry_time': entry_time,
            'direction': direction,
            'entry_price': entry_price,
            'exit_time': df['bt'].iloc[exit_idx],
            'pnl_pct': pnl_pct,
            'max_dd_pct': abs(worst_pnl),
            'hold_bars': hold,
            'won': pnl_pct > 0
        })
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

        direction = signals[i]
        entry_price = df['prc'].iloc[i]
        entry_time = df['bt'].iloc[i]

        trail_activated = False
        highest_fav = 0.0
        worst_dd = 0.0
        exited = False
        exit_price = entry_price
        exit_time = entry_time

        for j in range(i + 1, min(i + timeout_bars + 1, n)):
            h, l, c = df['hi'].iloc[j], df['lo'].iloc[j], df['prc'].iloc[j]

            if direction == 1:
                # LONG
                fav_pct = (h - entry_price) / entry_price * 100
                if fav_pct > highest_fav:
                    highest_fav = fav_pct
                if not trail_activated and highest_fav >= activation_pct:
                    trail_activated = True
                if trail_activated:
                    trailing_stop = highest_fav - trail_pct
                    stop_price = entry_price * (1 + trailing_stop / 100)
                    if l <= stop_price:
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
                if not trail_activated and highest_fav >= activation_pct:
                    trail_activated = True
                if trail_activated:
                    trailing_stop = highest_fav - trail_pct
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
            # Timeout → close at last bar close
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
        })
        i += 1

    return trades


# ─── Metrics ────────────────────────────────────────────────────

def compute_netp80(pnl_list):
    if len(pnl_list) == 0:
        return 0.0
    arr = np.array(pnl_list)
    p80 = np.percentile(arr, 80)
    p20 = np.percentile(arr, 20)
    return p80 - abs(p20)


def summarize(trades, label):
    if len(trades) == 0:
        return {
            'Label': label,
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

    return {
        'Label': label,
        'Trades': len(trades),
        'WR(%)': round(wins / len(trades) * 100, 2),
        'Mean Return(%)': round(np.mean(pnls), 4),
        'Avg DD(%)': round(np.mean(dds), 4),
        'Avg Hold(bars)': round(np.mean(holds), 2),
        'NetP80': round(compute_netp80(pnls), 4),
    }


# ─── Main ───────────────────────────────────────────────────────

def main():
    all_results = []

    for ticker in TICKERS:
        print(f"\n{'#'*70}", flush=True)
        print(f"# {ticker} — Fetching data...", flush=True)
        print(f"{'#'*70}", flush=True)

        df = fetch_5m_data(ticker)
        if len(df) == 0:
            print(f"  ERROR: No data for {ticker}", flush=True)
            continue
        print(f"  Rows: {len(df)}", flush=True)
        print(f"  Period: {df['bt'].min()} to {df['bt'].max()}", flush=True)

        for sig_name, sig_fn in SIGNALS:
            print(f"\n  ── Signal: {sig_name}", flush=True)
            try:
                signals = sig_fn(df)
            except Exception as e:
                print(f"     ERROR computing signal: {e}", flush=True)
                continue

            n_signals = np.sum(signals != 0)
            n_long = np.sum(signals == 1)
            n_short = np.sum(signals == -1)
            print(f"     Signals: {n_signals} total ({n_long} long, {n_short} short)", flush=True)

            if n_signals == 0:
                label = f"{ticker} | {sig_name} | Baseline(12)"
                all_results.append(summarize([], label))
                label = f"{ticker} | {sig_name} | TrailingTP"
                all_results.append(summarize([], label))
                continue

            # Baseline
            baseline_trades = run_baseline(df, signals, hold_bars=12)
            bl = summarize(baseline_trades, f"{ticker} | {sig_name} | Baseline(12)")
            all_results.append(bl)
            print(f"     Baseline: {bl['Trades']} trades, WR={bl['WR(%)']}%, Mean={bl['Mean Return(%)']}%, NetP80={bl['NetP80']}", flush=True)

            # Trailing TP
            trailing_trades = run_trailing_tp(df, signals, activation_pct=0.5, trail_pct=0.3)
            tp = summarize(trailing_trades, f"{ticker} | {sig_name} | TrailingTP")
            all_results.append(tp)
            print(f"     TrailingTP: {tp['Trades']} trades, WR={tp['WR(%)']}%, Mean={tp['Mean Return(%)']}%, NetP80={tp['NetP80']}", flush=True)

    # ─── Output table ───────────────────────────────────────────
    print(f"\n\n{'='*140}")
    print("COMPARISON TABLE: ALL 7 SIGNALS × 3 TICKERS × 2 METHODS")
    print(f"{'='*140}")

    headers = ['Ticker', 'Signal', 'Method', 'Trades', 'WR(%)', 'Mean Ret%', 'Avg DD%', 'Avg Hold', 'NetP80']
    header_fmt = "{:<6} {:<28} {:<14} {:>7} {:>8} {:>10} {:>10} {:>10} {:>10}"
    sep_fmt = "{:<6} {:<28} {:<14} {:>7} {:>8} {:>10} {:>10} {:>10} {:>10}"

    print(header_fmt.format(*headers))
    print("-" * 140)

    for r in all_results:
        label = r['Label']
        # Parse label: "Ticker | Signal | Method"
        parts = label.split(' | ')
        ticker = parts[0] if len(parts) >= 1 else label
        signal = parts[1] if len(parts) >= 2 else ""
        method = parts[2] if len(parts) >= 3 else ""
        print(sep_fmt.format(
            ticker,
            signal,
            method,
            r['Trades'],
            r['WR(%)'],
            r['Mean Return(%)'],
            r['Avg DD(%)'],
            r['Avg Hold(bars)'],
            r['NetP80']
        ))

    print("-" * 140)

    # ─── Also save to CSV ───────────────────────────────────────
    rows_out = []
    for r in all_results:
        parts = r['Label'].split(' | ')
        rows_out.append({
            'Ticker': parts[0] if len(parts) >= 1 else r['Label'],
            'Signal': parts[1] if len(parts) >= 2 else '',
            'Method': parts[2] if len(parts) >= 3 else '',
            'Trades': r['Trades'],
            'WR(%)': r['WR(%)'],
            'Mean_Ret%': r['Mean Return(%)'],
            'Avg_DD%': r['Avg DD(%)'],
            'Avg_Hold_bars': r['Avg Hold(bars)'],
            'NetP80': r['NetP80'],
        })

    out_df = pd.DataFrame(rows_out)
    csv_path = '/home/user/all_7_signals_results.csv'
    out_df.to_csv(csv_path, index=False)
    print(f"\nSaved to {csv_path}")

    # JSON for parent
    print(f"\nJSON output:")
    print(json.dumps(rows_out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
