#!/usr/bin/env python3
"""
OI Flat + Volume Explosion (churn) pattern test on MOEX futures.
Tests: Si, GZ, CR | Period: 2024-10-01 to today

Method:
  1. OI flat over 5 bars (25 min): |oi - oi.shift(5)| / oi.shift(5) < 0.01
  2. Volume surge: vol / rolling(20).mean() > 2.0
  3. Signal: oi_flat AND vol_surge
  4. Trend via SMA(10) — if trend up → SHORT, if trend down → LONG
  5. Forward return: 3, 6, 12 bars
  6. Metrics: WR, mean, Net80 (trim worst 20%)
  7. If WR < 52%: no edge, signal invalid
"""

import requests
import pandas as pd
import numpy as np
from datetime import date

CLICKHOUSE_URL = "http://10.0.0.60:8123/"
TICKERS = ["Si", "GZ", "CR"]
START_DATE = "2024-10-01"
END_DATE = date.today().isoformat()


def query_clickhouse(ticker):
    sql = f"""
    SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
           argMax(pr_close,SYSTIME) as prc,
           sum(vol) as vol,
           argMax(oi_close,SYSTIME) as oi
    FROM moex.tradestats_fo
    WHERE secid LIKE '{ticker}%'
      AND SYSTIME >= '{START_DATE}'
      AND SYSTIME <= '{END_DATE} 23:59:59'
      AND oi_close > 0
    GROUP BY bt
    ORDER BY bt
    FORMAT CSVWithNames
    """
    resp = requests.post(CLICKHOUSE_URL, data=sql.encode('utf-8'), timeout=120)
    resp.raise_for_status()
    if not resp.text.strip():
        return pd.DataFrame()
    df = pd.read_csv(pd.io.common.StringIO(resp.text))
    df.columns = [c.strip() for c in df.columns]
    df['bt'] = pd.to_datetime(df['bt'])
    df = df.sort_values('bt').reset_index(drop=True)
    return df


def detect_churn(df, ticker):
    if len(df) < 50:
        return None

    # OI flat over 5 bars
    oi_prev = df['oi'].shift(5)
    df['oi_change_pct'] = (df['oi'] - oi_prev).abs() / oi_prev
    df['oi_flat'] = df['oi_change_pct'] < 0.01

    # Volume surge
    df['vol_ma'] = df['vol'].rolling(20, min_periods=10).mean()
    df['vol_ratio'] = df['vol'] / df['vol_ma']
    df['vol_surge'] = df['vol_ratio'] > 2.0

    # Signal
    df['signal'] = df['oi_flat'] & df['vol_surge']

    # Trend via SMA(10) direction
    df['sma10'] = df['prc'].rolling(10).mean()
    df['trend_up'] = df['sma10'] > df['sma10'].shift(3)

    # Direction: trend up → SHORT, trend down → LONG
    df['direction'] = np.where(df['signal'] & df['trend_up'], 'SHORT',
                      np.where(df['signal'] & ~df['trend_up'], 'LONG', None))

    # Forward returns
    for fwd in [3, 6, 12]:
        df[f'fwd_ret_{fwd}'] = df['prc'].shift(-fwd) / df['prc'] - 1.0

    signals = df[df['signal']].copy()
    if len(signals) == 0:
        return {
            'ticker': ticker, 'total_signals': 0,
            'wr_3': None, 'mean_ret_3': None, 'net80_3': None,
            'wr_6': None, 'mean_ret_6': None, 'net80_6': None,
            'wr_12': None, 'mean_ret_12': None, 'net80_12': None,
            'message': 'No churn signals detected'
        }

    results = {'ticker': ticker, 'total_signals': len(signals)}

    for fwd in [3, 6, 12]:
        col = f'fwd_ret_{fwd}'
        valid = signals[signals[col].notna()].copy()
        if len(valid) < 5:
            results[f'wr_{fwd}'] = None
            results[f'mean_ret_{fwd}'] = None
            results[f'net80_{fwd}'] = None
            continue

        # SHORT → correct if fwd_ret < 0; LONG → correct if fwd_ret > 0
        valid['correct'] = np.where(
            (valid['direction'] == 'SHORT') & (valid[col] < 0), True,
            np.where((valid['direction'] == 'LONG') & (valid[col] > 0), True, False)
        )

        wr = valid['correct'].mean() * 100
        mean_ret = valid[col].mean() * 100

        # Net80 = top 80% (trim worst 20%)
        sorted_ret = valid[col].sort_values()
        trim_idx = max(1, int(len(sorted_ret) * 0.2))
        net80 = sorted_ret.iloc[trim_idx:].mean() * 100

        results[f'wr_{fwd}'] = round(wr, 1)
        results[f'mean_ret_{fwd}'] = round(mean_ret, 3)
        results[f'net80_{fwd}'] = round(net80, 3)

    return results


def main():
    print(f"OI Flat + Volume Explosion (Churn) Pattern Test")
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"Tickers: {', '.join(TICKERS)}")
    print("=" * 100)

    all_results = []
    for ticker in TICKERS:
        print(f"\n--- Fetching {ticker} data... ", end="", flush=True)
        try:
            df = query_clickhouse(ticker)
            print(f"{len(df)} rows")
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        res = detect_churn(df, ticker)
        all_results.append(res)

        if res['total_signals'] == 0:
            print(f"  {res['message']}")
            continue

        print(f"  Total signals: {res['total_signals']}")
        print(f"  {'Forward':>8} | {'WR%':>6} | {'Mean Ret%':>10} | {'Net80%':>8}")
        print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*10}-+-{'-'*8}")
        for fwd in [3, 6, 12]:
            wr = res.get(f'wr_{fwd}', 'N/A')
            mr = res.get(f'mean_ret_{fwd}', 'N/A')
            n8 = res.get(f'net80_{fwd}', 'N/A')
            wr_str = f"{wr:.1f}%" if wr is not None else "N/A"
            mr_str = f"{mr:.4f}" if mr is not None else "N/A"
            n8_str = f"{n8:.4f}" if n8 is not None else "N/A"
            print(f"  {fwd:>8} | {wr_str:>6} | {mr_str:>10} | {n8_str:>8}")

        if res.get('wr_3') is not None and res['wr_3'] < 52:
            print(f"  ⚠ WR < 52% — signal is INVALID (no edge)")

    # Summary
    print("\n" + "=" * 100)
    print("SUMMARY TABLE")
    print(f"{'Ticker':>6} | {'Signals':>7} | {'WR(3)%':>7} | {'Mean3%':>8} | {'Net80(3)%':>9} | {'WR(6)%':>7} | {'Mean6%':>8} | {'Net80(6)%':>9} | {'WR(12)%':>7} | {'Mean12%':>8} | {'Net80(12)%':>9}")
    print("-" * 100)
    for res in all_results:
        if res is None:
            continue
        t = res['ticker']
        sig = res['total_signals']
        def fmt(key):
            v = res.get(key)
            if v is None:
                return "N/A"
            if key.startswith('wr_'):
                return f"{v:.1f}"
            return f"{v:.4f}"
        line = f"{t:>6} | {sig:>7} | {fmt('wr_3'):>7} | {fmt('mean_ret_3'):>8} | {fmt('net80_3'):>9}"
        line += f" | {fmt('wr_6'):>7} | {fmt('mean_ret_6'):>8} | {fmt('net80_6'):>9}"
        line += f" | {fmt('wr_12'):>7} | {fmt('mean_ret_12'):>8} | {fmt('net80_12'):>9}"
        print(line)
        # Validation
        wr3 = res.get('wr_3')
        wr6 = res.get('wr_6')
        wr12 = res.get('wr_12')
        verdicts = []
        for w, h in [(wr3, '3b'), (wr6, '6b'), (wr12, '12b')]:
            if w is None:
                verdicts.append(f"{h}:N/A")
            elif w < 52:
                verdicts.append(f"{h}:INVALID({w:.1f}%)")
            else:
                verdicts.append(f"{h}:OK({w:.1f}%)")
        print(f"       Verdict: {', '.join(verdicts)}")

    print("=" * 100)
    print("WR threshold: ≥ 52% required for valid edge")


if __name__ == '__main__':
    main()
