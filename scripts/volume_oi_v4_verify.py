#!/usr/bin/env python3
"""
Walk-forward verification for Volume × OI yur_accumulation (Variant 4).
Tests best combos across 4 folds (75/25 time split, 4 different test windows).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB
from pathlib import Path

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

COMMISSION = 2
EXIT_YZ = 0.5
STOP_LOSS = 0.02
CFG = {'minstep': 0.01, 'tick_rub': 1.0, 'go': 5000}

TICKERS = {
    'PD': {'vol_z': 3.0, 'yur_z': 1.0, 'atr': 1.0, 'hold': 48},
    'GL': {'vol_z': 3.5, 'yur_z': 1.5, 'atr': 0.5, 'hold': 48},
    'GD': {'vol_z': 3.5, 'yur_z': 1.0, 'atr': 1.0, 'hold': 12},
    'CC': {'vol_z': 3.5, 'yur_z': 1.0, 'atr': 1.0, 'hold': 12},
    'IB': {'vol_z': 3.5, 'yur_z': 2.0, 'atr': 1.0, 'hold': 12},
}

DAYS = 400
SINCE = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')


def rolling_zs(vals, w=20):
    s = pd.Series(vals).ffill()
    mu = s.rolling(w, min_periods=w // 2).mean()
    sd = s.rolling(w, min_periods=w // 2).std().replace(0, 1)
    return ((s - mu) / sd).fillna(0)


def compute_atr(high, low, close, period=14):
    close_s = pd.Series(close)
    tr = pd.Series(np.maximum(
        high - low,
        np.maximum(
            np.abs(high - close_s.shift(1).values),
            np.abs(low - close_s.shift(1).values)
        )
    ))
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr.values


def calc_pnl_rub(entry, exit_price, cfg):
    moves = (exit_price - entry) / cfg['minstep']
    return moves * cfg['tick_rub']


def max_dd_from_equity(equity):
    if not equity or len(equity) < 2:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        mdd = max(mdd, dd)
    return mdd


def run_backtest(df, params):
    vol_z_th = params['vol_z']
    yur_z_th = params['yur_z']
    atr_th = params['atr']
    max_hold_val = params['hold']

    n_total = len(df)
    mask = (df['vol_z'] > vol_z_th) & (df['yur_z'] > yur_z_th) & (df['fiz_z'] < 0)
    sig_indices = df[mask].index.tolist()

    trades = []
    for idx in sig_indices:
        if atr_th is not None:
            atr_val = float(df.iloc[idx]['atr_pct'])
            if atr_val > atr_th:
                continue

        entry_idx = idx + 1
        if entry_idx >= n_total:
            continue
        entry_open = float(df.iloc[entry_idx]['open'])
        if entry_open <= 0:
            continue

        stop_price = entry_open * (1 - STOP_LOSS)
        max_exit_idx = entry_idx + max_hold_val
        if max_exit_idx >= n_total:
            continue

        exit_price = None
        for j in range(entry_idx + 1, max_exit_idx + 1):
            current_yz = float(df.iloc[j]['yur_z'])
            if current_yz < EXIT_YZ:
                exit_price = float(df.iloc[j]['close'])
                break
            bars_held = j - entry_idx
            if bars_held >= max_hold_val:
                exit_price = float(df.iloc[j]['close'])
                break
            low_j = float(df.iloc[j]['low'])
            if low_j <= stop_price:
                exit_price = float(df.iloc[j]['close'])
                break

        if exit_price is None:
            continue

        pnl = calc_pnl_rub(entry_open, exit_price, CFG)
        net_pnl = pnl - COMMISSION
        trades.append(net_pnl)

    n_trades = len(trades)
    if n_trades > 0:
        wins = [t for t in trades if t > 0]
        wr = len(wins) / n_trades * 100
        net = sum(trades)
        eq = [CFG['go']]
        for t in trades:
            eq.append(eq[-1] + t)
        mdd = max_dd_from_equity(eq) * 100
    else:
        wr = 0.0
        net = 0
        mdd = 0.0

    return n_trades, wr, net, mdd


# ── 1. Load data ──
print("=" * 80)
print("  VOLUME x OI — VARIANT 4: WALK-FORWARD VERIFICATION")
print("=" * 80)
print(f"\n[1] Loading data for {len(TICKERS)} tickers...\n")

all_data = {}
for ticker in TICKERS:
    print(f"  {ticker}...", end=' ', flush=True)
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m AS p
        INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0
        ORDER BY p.time
    """, parameters={'t': ticker, 's': SINCE}).result_rows

    if not rows or len(rows) < 200:
        print(f"SKIP: only {len(rows) if rows else 0} bars")
        continue

    df = pd.DataFrame(rows, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi'
    ])
    df['fiz_net'] = df['fiz_buy'] - df['fiz_sell']
    df['yur_net'] = df['yur_buy'] - df['yur_sell']
    df['vol_z'] = rolling_zs(df['volume'], 20)
    df['fiz_z'] = rolling_zs(df['fiz_net'], 20)
    df['yur_z'] = rolling_zs(df['yur_net'], 20)

    atr_vals = compute_atr(df['high'].values, df['low'].values, df['close'].values, 14)
    df['atr_pct'] = atr_vals / df['close'].values * 100

    all_data[ticker] = df
    print(f"{len(df):,} bars loaded ({df['time'].min().strftime('%Y-%m-%d')} → {df['time'].max().strftime('%Y-%m-%d')})")

print(f"\n  Loaded {len(all_data)}/{len(TICKERS)} tickers")

# ── 2. Walk-forward 4-fold ──
print(f"\n[2] Walk-forward 4-fold verification (75/25 by time)...\n")

out_dir = Path('reports/volume_oi_v4')
out_dir.mkdir(parents=True, exist_ok=True)

results = []
all_lines = []

for ticker, params in sorted(TICKERS.items()):
    if ticker not in all_data:
        continue

    df = all_data[ticker]
    n = len(df)
    param_str = f"vol_z={params['vol_z']}, yur_z={params['yur_z']}, ATR≤{params['atr']}%, hold={params['hold']}"

    # Split into 4 equal quarters by time
    seg_size = n // 4
    quarters = [
        (0, seg_size),
        (seg_size, 2 * seg_size),
        (2 * seg_size, 3 * seg_size),
        (3 * seg_size, n),
    ]

    fold_results = []

    for fold_i in range(4):
        # Test on quarter fold_i, use preceding + current data for z-score warm-up
        (ts, te) = quarters[fold_i]
        # Take data from start of dataset through end of test window
        # (ensures rolling z-scores are properly initialized before test window)
        eval_df = df.iloc[:te].reset_index(drop=True)
        # Only evaluate trades whose entry is within the test window
        test_start = ts
        test_end = te

        # Run backtest on eval_df but only keep trades where entry_bar >= test_start
        vol_z_th = params['vol_z']
        yur_z_th = params['yur_z']
        atr_th = params['atr']
        max_hold_val = params['hold']

        n_total = len(eval_df)
        mask = (eval_df['vol_z'] > vol_z_th) & (eval_df['yur_z'] > yur_z_th) & (eval_df['fiz_z'] < 0)
        sig_indices = eval_df[mask].index.tolist()

        trades = []
        entry_times = []
        for idx in sig_indices:
            if idx < test_start:
                continue
            if idx >= test_end:
                break

            if atr_th is not None:
                atr_val = float(eval_df.iloc[idx]['atr_pct'])
                if atr_val > atr_th:
                    continue

            entry_idx = idx + 1
            if entry_idx >= n_total:
                continue
            entry_open = float(eval_df.iloc[entry_idx]['open'])
            if entry_open <= 0:
                continue

            stop_price = entry_open * (1 - STOP_LOSS)
            max_exit_idx = entry_idx + max_hold_val
            if max_exit_idx >= n_total:
                continue

            exit_price = None
            for j in range(entry_idx + 1, max_exit_idx + 1):
                current_yz = float(eval_df.iloc[j]['yur_z'])
                if current_yz < EXIT_YZ:
                    exit_price = float(eval_df.iloc[j]['close'])
                    break
                bars_held = j - entry_idx
                if bars_held >= max_hold_val:
                    exit_price = float(eval_df.iloc[j]['close'])
                    break
                low_j = float(eval_df.iloc[j]['low'])
                if low_j <= stop_price:
                    exit_price = float(eval_df.iloc[j]['close'])
                    break

            if exit_price is None:
                continue

            pnl = calc_pnl_rub(entry_open, exit_price, CFG)
            net_pnl = pnl - COMMISSION
            trades.append(net_pnl)

        n_trades = len(trades)
        if n_trades > 0:
            wins = [t for t in trades if t > 0]
            wr = len(wins) / n_trades * 100
            net = sum(trades)
            eq = [CFG['go']]
            for t in trades:
                eq.append(eq[-1] + t)
            mdd = max_dd_from_equity(eq) * 100
        else:
            wr = 0.0
            net = 0
            mdd = 0.0

        fold_results.append({
            'fold': fold_i + 1,
            'trades': n_trades,
            'wr': wr,
            'net_pnl': net,
            'max_dd': mdd,
        })

    all_wr_ok = all(r['wr'] > 50 for r in fold_results)
    all_pnl_ok = all(r['net_pnl'] > 0 for r in fold_results)
    passed = all_wr_ok and all_pnl_ok
    total_net = sum(r['net_pnl'] for r in fold_results)

    results.append({
        'ticker': ticker,
        'params': param_str,
        'folds': fold_results,
        'passed': passed,
        'total_net': total_net,
    })

# ── 3. Build report ──
now_s = datetime.now().strftime('%Y-%m-%d %H:%M')
lines = []
lines.append("=" * 110)
lines.append("  VOLUME x OI — VARIANT 4: WALK-FORWARD VERIFICATION")
lines.append("=" * 110)
lines.append(f"\nDate: {now_s}")
lines.append(f"Data window: {DAYS} days (since {SINCE})")
lines.append(f"Commission: {COMMISSION} RUB/contract")
lines.append(f"Exit: yur_z < {EXIT_YZ} (adaptive)")
lines.append(f"Stop-loss: {STOP_LOSS*100:.0f}%")
lines.append(f"Position: 1 contract (flat sizing)")
lines.append(f"Walk-forward: 4-fold, 75/25 time split")
lines.append(f"Criterion: WR > 50% AND Net PnL > 0 in ALL 4 folds")
lines.append("")

print("\n" + "=" * 110)
print("  RESULTS")
print("=" * 110)

passed_count = 0
for res in results:
    hdr = f"\n{res['ticker']}  |  {res['params']}"
    print(hdr)
    lines.append(hdr)
    lines.append("-" * 110)
    lines.append(f"{'Fold':>6} | {'Trades':>7} | {'WR%':>7} | {'Net PnL':>10} | {'Max DD%':>8}")
    lines.append("-" * 110)

    for fr in res['folds']:
        ln = (f"{fr['fold']:>6} | {fr['trades']:>7d} | {fr['wr']:>6.2f}% | "
              f"{fr['net_pnl']:>+10.0f} | {fr['max_dd']:>7.2f}%")
        print(ln)
        lines.append(ln)

    verdict = ">> PASSED" if res['passed'] else ">> FAILED"
    reason = ""
    if not res['passed']:
        failures = []
        for fr in res['folds']:
            if fr['wr'] <= 50:
                failures.append(f"Fold {fr['fold']} WR={fr['wr']:.1f}%")
            if fr['net_pnl'] <= 0:
                failures.append(f"Fold {fr['fold']} PnL={fr['net_pnl']:.0f}")
        reason = " — " + "; ".join(failures)
    summary = f"  {verdict}{reason}"
    if res['passed']:
        summary += f" | Sum Net PnL: {res['total_net']:+10.0f}"
        passed_count += 1
    print(summary)
    lines.append(summary)
    lines.append("")

print()
print("=" * 110)
print("  SUMMARY")
print("=" * 110)
lines.append("=" * 110)
lines.append("  SUMMARY")
lines.append("=" * 110)
lines.append("")
hline = f"{'Ticker':>6} | {'Result':>8} | {'WR>50% in':>11} | {'PnL>0 in':>10} | {'Total Net PnL':>13}"
print(hline)
lines.append(hline)
print("-" * 110)
lines.append("-" * 110)

for res in results:
    wr_ok = sum(1 for r in res['folds'] if r['wr'] > 50)
    pnl_ok = sum(1 for r in res['folds'] if r['net_pnl'] > 0)
    status = "PASS" if res['passed'] else "FAIL"
    ln = (f"{res['ticker']:>6} | {status:>8} | {wr_ok:>3}/4 folds{'':>3} | "
          f"{pnl_ok:>3}/4 folds{'':>2} | {res['total_net']:>+10.0f}")
    print(ln)
    lines.append(ln)

print()
print(f"Passed: {passed_count}/{len(results)}")
print(f"Failed: {len(results) - passed_count}/{len(results)}")
lines.append("")
lines.append(f"Passed: {passed_count}/{len(results)}")
lines.append(f"Failed: {len(results) - passed_count}/{len(results)}")
lines.append("")

if passed_count == 0:
    final = ("\nНИ ОДИН ТИКЕР НЕ ПРОШЁЛ верификацию. "
             "Walk-forward показал, что лучшие комбинации из v4 "
             "нестабильны во времени — WR ≤ 50% или PnL ≤ 0 хотя бы в одном из 4 folds.")
    print(final)
    lines.append(final)

lines.append("")
lines.append("=" * 110)
lines.append("  END OF REPORT")
lines.append("=" * 110)

report = '\n'.join(lines)
out_path = out_dir / 'verify.txt'
with open(out_path, 'w') as f:
    f.write(report)
print(f"\nReport saved to {out_path}")
print("\nDone.")
