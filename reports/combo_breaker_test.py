#!/usr/bin/env python3
"""Disb_z + OI_z Combo Breaker — test on MOEX futures (Si, GZ, CR)"""

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from datetime import datetime, date
import clickhouse_connect

# ── Config ──
TICKERS = ['Si', 'GZ', 'CR']
START = '2024-10-01'
END = date.today().isoformat()

CLICKHOUSE_HOST = '10.0.0.60'
CLICKHOUSE_PORT = 8123

# ── Connect ──
client = clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT, database='moex')

# ── Helpers ──
def zscore(series, window):
    """Rolling z-score"""
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    return (series - mean) / std.replace(0, np.nan)

def compute_strategy(df, ticker):
    df = df.copy()
    # Disbalance
    total = df['vb'] + df['vs']
    total = total.replace(0, np.nan)
    df['disb'] = (df['vb'] - df['vs']).abs() / total
    df['z_disb'] = zscore(df['disb'], 40)
    df['z_oi'] = zscore(df['oi'], 40)
    df['sma20'] = df['prc'].rolling(20, min_periods=20).mean()

    # Signal
    cond_high = (df['z_oi'] > 1.0) & (df['z_disb'] > 1.5)
    df['signal'] = 0
    df.loc[cond_high & (df['prc'] < df['sma20']), 'signal'] = 1   # LONG — fade down move, exhaustion
    df.loc[cond_high & (df['prc'] > df['sma20']), 'signal'] = -1  # SHORT — fade up move, exhaustion

    # Forward returns (%)
    for fwd in [3, 6, 12]:
        df[f'ret_{fwd}'] = df['prc'].pct_change(periods=fwd).shift(-fwd) * 100

    # Metrics
    results = []
    for fwd in [3, 6, 12]:
        col = f'ret_{fwd}'
        sig_df = df[df['signal'] != 0].dropna(subset=[col])
        if len(sig_df) == 0:
            results.append({
                'ticker': ticker, 'period': f'{fwd}bar',
                'n_signals': 0, 'n_long': 0, 'n_short': 0,
                'win_rate': np.nan, 'avg_return': np.nan,
                'total_pnl': np.nan, 'max_dd': np.nan, 'sharpe': np.nan,
            })
            continue

        # Signal-aligned return: +1 for long (price up = win), -1 for short (price down = win)
        sig_df = sig_df.copy()
        sig_df['sig_ret'] = sig_df['signal'] * sig_df[col]
        wins = (sig_df['sig_ret'] > 0).sum()
        total_sig = len(sig_df)
        wr = wins / total_sig * 100

        long_mask = sig_df['signal'] == 1
        short_mask = sig_df['signal'] == -1
        long_sigs = sig_df.loc[long_mask, col]
        short_sigs = sig_df.loc[short_mask, col]

        avg_ret = sig_df['sig_ret'].mean()
        avg_long = long_sigs.mean() if len(long_sigs) > 0 else np.nan
        # For shorts: negative short_ret means price dropped → win, but we want "average short gain" as positive
        short_gain = -short_sigs if len(short_sigs) > 0 else np.nan
        avg_short = short_gain.mean() if isinstance(short_gain, pd.Series) and len(short_gain) > 0 else np.nan

        total_pnl = sig_df['sig_ret'].sum()
        cum_ret = sig_df['sig_ret'].cumsum()
        max_dd = (cum_ret.cummax() - cum_ret).max()

        # Annualized sharpe: ~288 5min bars per day, ~252 trading days
        if sig_df['sig_ret'].std() > 0:
            # periods per year = 288 * 252
            # for fwd periods: scale factor = sqrt(288*252/fwd)
            ann_factor = np.sqrt(288 * 252 / fwd)
            sharpe = sig_df['sig_ret'].mean() / sig_df['sig_ret'].std() * ann_factor
        else:
            sharpe = np.nan

        results.append({
            'ticker': ticker, 'period': f'{fwd}bar',
            'n_signals': total_sig,
            'n_long': int(long_mask.sum()),
            'n_short': int(short_mask.sum()),
            'win_rate': round(wr, 2),
            'avg_return': round(avg_ret, 4),
            'avg_long_ret': round(avg_long, 4) if not np.isnan(avg_long) else np.nan,
            'avg_short_gain': round(avg_short, 4) if not (isinstance(avg_short, float) and np.isnan(avg_short)) else np.nan,
            'total_pnl': round(total_pnl, 4),
            'max_dd': round(max_dd, 4),
            'sharpe': round(sharpe, 4) if not np.isnan(sharpe) else np.nan,
        })

    return df, pd.DataFrame(results)


# ── Main ──
print("=" * 85)
print("Disb_z + OI_z COMBO BREAKER — MOEX Futures Test")
print(f"Period: {START} to {END}  |  Interval: 5min")
print("=" * 85)

all_results = []

for ticker in TICKERS:
    print(f"\n{'─' * 65}")
    print(f"📊 {ticker}")
    print(f"{'─' * 65}")

    query = f"""
    SELECT
        toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
        argMax(pr_close, SYSTIME) as prc,
        sum(vol_b) as vb,
        sum(vol_s) as vs,
        argMax(oi_close, SYSTIME) as oi
    FROM moex.tradestats_fo
    WHERE secid LIKE '{ticker}%'
      AND SYSTIME >= '{START}'
      AND oi_close > 0
    GROUP BY bt
    ORDER BY bt
    """

    try:
        rows = client.query(query)
        col_names = ['bt', 'prc', 'vb', 'vs', 'oi']
        data = rows.result_rows

        if not data or len(data) == 0:
            print(f"  ⚠️  No data")
            continue

        df = pd.DataFrame(data, columns=col_names)
        # Convert bt column (ClickHouse datetime) to pandas datetime
        df['bt'] = pd.to_datetime(df['bt'])
        print(f"  Rows: {len(df):,}  |  {df['bt'].min().strftime('%Y-%m-%d')} → {df['bt'].max().strftime('%Y-%m-%d')}")
        print(f"  Price: {df['prc'].min():.2f} → {df['prc'].max():.2f}  |  Avg OI: {df['oi'].mean():.0f}")

        df_result, metrics = compute_strategy(df, ticker)
        all_results.append(metrics)

        for _, row in metrics.iterrows():
            wr = row['win_rate']
            if np.isnan(wr):
                flag = "⚠️"
                wr_str = " N/A "
            elif wr >= 52:
                flag = "✅"
                wr_str = f"{wr:.2f}% ✓"
            else:
                flag = "❌"
                wr_str = f"{wr:.2f}% ✗"

            sharpe_str = f"{row['sharpe']:.2f}" if not np.isnan(row['sharpe']) else " N/A"

            print(f"  {flag} {row['period']:8s}  "
                  f"N={row['n_signals']:4d}  "
                  f"L={row['n_long']:3d}/S={row['n_short']:3d}  "
                  f"WR={wr_str:>8s}  "
                  f"AvgRet={row['avg_return']:>+7.4f}%  "
                  f"PnL={row['total_pnl']:>+8.4f}%  "
                  f"Sharpe={sharpe_str}")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()

if all_results:
    print(f"\n{'=' * 85}")
    print("📋  SUMMARY TABLE")
    print(f"{'=' * 85}")
    print(f"  {'Ticker':6s} {'Period':8s} {'Signals':>8s} {'L/S':>7s} {'WinRate':>8s} {'AvgRet':>8s} {'PnL':>10s} {'Sharpe':>7s}")
    print(f"  {'─'*6} {'─'*8} {'─'*8} {'─'*7} {'─'*8} {'─'*8} {'─'*10} {'─'*7}")

    summary = pd.concat(all_results, ignore_index=True)

    for _, row in summary.iterrows():
        wr = row['win_rate']
        if np.isnan(wr):
            flag = "⚠️"
            wr_str = "N/A"
        elif wr >= 52:
            flag = "✅"
            wr_str = f"{wr:.2f}%"
        else:
            flag = "❌"
            wr_str = f"{wr:.2f}%"

        sharpe_str = f"{row['sharpe']:.2f}" if not np.isnan(row['sharpe']) else "N/A"
        avg_str = f"{row['avg_return']:+.4f}%" if not np.isnan(row['avg_return']) else "N/A"
        pnl_str = f"{row['total_pnl']:+.4f}%" if not np.isnan(row['total_pnl']) else "N/A"

        print(f"  {flag} {row['ticker']:4s}   {row['period']:8s}  "
              f"{row['n_signals']:4d}     "
              f"{row['n_long']:d}/{row['n_short']:d}   "
              f"{wr_str:>7s}  "
              f"{avg_str:>8s}  "
              f"{pnl_str:>10s}  "
              f"{sharpe_str:>6s}")

    print(f"\n{'=' * 85}")
    print("CONDITIONS:")
    print("  LONG:  z_oi > 1.0 AND z_disb > 1.5 AND close < SMA(20)  → fade downside exhaustion")
    print("  SHORT: z_oi > 1.0 AND z_disb > 1.5 AND close > SMA(20)  → fade upside exhaustion")
    print("  WR < 52% → no reliable signal")
    print(f"{'=' * 85}")
else:
    print("\n❌ No data retrieved for any ticker")
