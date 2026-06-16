#!/usr/bin/env python3
"""
Volume x OI — Variant 5: Smart Fractional Sizing.
Размер лота = функция от качества сигнала:
  SCORE = min(vol_z/MAX_VOL + yur_z/MAX_YUR, 1.0) / 2
  contracts = max(1, int(SCORE * MAX_CONTRACTS))
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

TICKERS = ['PD', 'CC', 'IB']
DAYS = 400
SINCE = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')
COMMISSION = 2
EXIT_YZ = 0.5
MAX_HOLD = 48
STOP_LOSS = 0.02
ATR_FILTER = 1.0

TICKER_PARAMS = {
    'PD': {'vol_z': 3.0, 'yur_z': 1.5, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'vol_z': 3.5, 'yur_z': 1.5, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'IB': {'vol_z': 3.5, 'yur_z': 2.0, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}

MAX_CONTRACTS_VALS = [1, 2, 3, 5]
MAX_VOL_VALS = [5.0, 8.0]
MAX_YUR_VALS = [3.0, 5.0]


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


# ── 1. Load data ──
print("=" * 80)
print("  VOLUME x OI — VARIANT 5: SMART FRACTIONAL SIZING")
print("=" * 80)
print(f"\n[1] Loading data for {len(TICKERS)} tickers...")

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
    print(f"{len(df):,} bars loaded")

ticker_list = [t for t in TICKERS if t in all_data]
print(f"\n  Successfully loaded: {len(ticker_list)}/{len(TICKERS)} tickers")

# ── 2. Find signals ──
print(f"\n[2] Finding signals...")

all_signals = {}
for ticker in ticker_list:
    df = all_data[ticker]
    p = TICKER_PARAMS[ticker]

    mask = (df['vol_z'] > p['vol_z']) & (df['yur_z'] > p['yur_z']) & (df['fiz_z'] < 0)
    sig_indices = df[mask].index.tolist()

    signals = []
    for idx in sig_indices:
        if ATR_FILTER is not None:
            atr_val = float(df.iloc[idx]['atr_pct'])
            if atr_val > ATR_FILTER:
                continue

        entry_idx = idx + 1
        if entry_idx >= len(df):
            continue
        entry_open = float(df.iloc[entry_idx]['open'])
        if entry_open <= 0:
            continue

        signals.append({
            'entry_idx': entry_idx,
            'entry_open': entry_open,
            'signal_vol_z': float(df.iloc[idx]['vol_z']),
            'signal_yur_z': float(df.iloc[idx]['yur_z']),
        })

    all_signals[ticker] = signals
    print(f"  {ticker}: {len(signals)} signals")

# ── 3. Simulation ──
print(f"\n[3] Running simulations...")

results = []

for ticker in ticker_list:
    df = all_data[ticker]
    cfg = TICKER_PARAMS[ticker]
    signals = all_signals[ticker]
    n_total = len(df)

    # ── Flat 1 contract (control) ──
    trades_flat = []
    for sig in signals:
        i = sig['entry_idx']
        entry = sig['entry_open']
        stop_price = entry * (1 - STOP_LOSS)
        max_idx = i + MAX_HOLD
        if max_idx >= n_total:
            continue

        exit_price = None
        for j in range(i + 1, max_idx + 1):
            current_yz = float(df.iloc[j]['yur_z'])
            if current_yz < EXIT_YZ:
                exit_price = float(df.iloc[j]['close'])
                break
            if (j - i) >= MAX_HOLD:
                exit_price = float(df.iloc[j]['close'])
                break
            low_j = float(df.iloc[j]['low'])
            if low_j <= stop_price:
                exit_price = float(df.iloc[j]['close'])
                break

        if exit_price is None:
            continue

        pnl = calc_pnl_rub(entry, exit_price, cfg)
        net_pnl = pnl - COMMISSION
        trades_flat.append(net_pnl)

    n_trades = len(trades_flat)
    if n_trades > 0:
        wins = [t for t in trades_flat if t > 0]
        wr = len(wins) / n_trades * 100
        net = sum(trades_flat)
        eq = [cfg['go']]
        for t in trades_flat:
            eq.append(eq[-1] + t)
        mdd = max_dd_from_equity(eq) * 100
    else:
        wr = 0.0
        net = 0
        mdd = 0.0

    results.append({
        'ticker': ticker,
        'max_contracts': 1,
        'max_vol': 0.0,
        'max_yur': 0.0,
        'sizing': 'FLAT',
        'trades': n_trades,
        'wr': wr,
        'net_pnl': net,
        'max_dd': mdd,
    })

    # ── Fractional sizing combos ──
    for max_cont in MAX_CONTRACTS_VALS:
        for max_vol in MAX_VOL_VALS:
            for max_yur in MAX_YUR_VALS:
                trades = []
                for sig in signals:
                    sv = sig['signal_vol_z']
                    sy = sig['signal_yur_z']
                    score = min(sv / max_vol + sy / max_yur, 1.0) / 2.0
                    contracts = max(1, int(score * max_cont))

                    i = sig['entry_idx']
                    entry = sig['entry_open']
                    stop_price = entry * (1 - STOP_LOSS)
                    max_idx = i + MAX_HOLD
                    if max_idx >= n_total:
                        continue

                    exit_price = None
                    for j in range(i + 1, max_idx + 1):
                        current_yz = float(df.iloc[j]['yur_z'])
                        if current_yz < EXIT_YZ:
                            exit_price = float(df.iloc[j]['close'])
                            break
                        if (j - i) >= MAX_HOLD:
                            exit_price = float(df.iloc[j]['close'])
                            break
                        low_j = float(df.iloc[j]['low'])
                        if low_j <= stop_price:
                            exit_price = float(df.iloc[j]['close'])
                            break

                    if exit_price is None:
                        continue

                    pnl = calc_pnl_rub(entry, exit_price, cfg)
                    net_pnl = pnl * contracts - COMMISSION * contracts
                    trades.append(net_pnl)

                n_trades = len(trades)
                if n_trades > 0:
                    wins = [t for t in trades if t > 0]
                    wr = len(wins) / n_trades * 100
                    net = sum(trades)
                    eq = [cfg['go']]
                    for t in trades:
                        eq.append(eq[-1] + t)
                    mdd = max_dd_from_equity(eq) * 100
                else:
                    wr = 0.0
                    net = 0
                    mdd = 0.0

                results.append({
                    'ticker': ticker,
                    'max_contracts': max_cont,
                    'max_vol': max_vol,
                    'max_yur': max_yur,
                    'sizing': f'FRAC_{max_cont}_{max_vol}_{max_yur}',
                    'trades': n_trades,
                    'wr': wr,
                    'net_pnl': net,
                    'max_dd': mdd,
                })

    print(f"  {ticker}: flat={len(trades_flat)} trades, {len(MAX_CONTRACTS_VALS)*len(MAX_VOL_VALS)*len(MAX_YUR_VALS)} fractional combos done")

# ── 4. Report ──
print(f"\n[4] Generating report...")

out_dir = Path('reports/volume_oi_v5')
out_dir.mkdir(parents=True, exist_ok=True)

lines = []
lines.append("=" * 100)
lines.append("  VOLUME x OI — VARIANT 5: SMART FRACTIONAL SIZING — RESULTS")
lines.append("=" * 100)
lines.append(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append(f"Tickers: {', '.join(ticker_list)}")
lines.append(f"Data window: {DAYS} days (since {SINCE})")
lines.append(f"Commission: {COMMISSION} RUB/contract")
lines.append(f"Exit yur_z threshold: {EXIT_YZ}")
lines.append(f"Stop-loss: {STOP_LOSS*100:.0f}%")
lines.append(f"Max hold: {MAX_HOLD} bars")
lines.append(f"ATR filter: ≤{ATR_FILTER}%")
lines.append("")
lines.append("Fractional sizing formula:")
lines.append("  SCORE = min(vol_z/MAX_VOL + yur_z/MAX_YUR, 1.0) / 2")
lines.append("  contracts = max(1, int(SCORE * MAX_CONTRACTS))")
lines.append("")
lines.append("Grid:")
lines.append(f"  MAX_CONTRACTS: {MAX_CONTRACTS_VALS}")
lines.append(f"  MAX_VOL: {MAX_VOL_VALS}")
lines.append(f"  MAX_YUR: {MAX_YUR_VALS}")
lines.append("")

df_res = pd.DataFrame(results)

# ── Per-ticker tables ──
for ticker in ticker_list:
    sub = df_res[df_res['ticker'] == ticker]

    lines.append("-" * 100)
    lines.append(f"  {ticker}")
    lines.append("-" * 100)

    flat = sub[sub['sizing'] == 'FLAT']
    frac = sub[sub['sizing'] != 'FLAT']

    if len(flat) > 0:
        fr = flat.iloc[0]
        lines.append(f"  FLAT (1 contract): Trades={fr['trades']}, WR={fr['wr']:.1f}%, "
                     f"Net PnL={fr['net_pnl']:+8.0f}, Max DD={fr['max_dd']:.2f}%")

    if len(frac) > 0:
        best = frac.sort_values('net_pnl', ascending=False).iloc[0]
        worst = frac.sort_values('net_pnl', ascending=True).iloc[0]
        lines.append(f"  FRACTIONAL BEST:  MC={best['max_contracts']:.0f} MV={best['max_vol']:.1f} MY={best['max_yur']:.1f} "
                     f"| Trades={best['trades']} WR={best['wr']:.1f}% Net={best['net_pnl']:+8.0f} DD={best['max_dd']:.2f}%")
        lines.append(f"  FRACTIONAL WORST: MC={worst['max_contracts']:.0f} MV={worst['max_vol']:.1f} MY={worst['max_yur']:.1f} "
                     f"| Trades={worst['trades']} WR={worst['wr']:.1f}% Net={worst['net_pnl']:+8.0f} DD={worst['max_dd']:.2f}%")

    lines.append(f"\n  {'Contracts':>9} {'MAX_VOL':>7} {'MAX_YUR':>7} {'Trades':>7} {'WR%':>6} {'Net PnL':>10} {'Max DD%':>8}")
    lines.append(f"  {'-'*9:>9} {'-'*7:>7} {'-'*7:>7} {'-'*7:>7} {'-'*6:>6} {'-'*10:>10} {'-'*8:>8}")

    for _, r in frac.sort_values('net_pnl', ascending=False).iterrows():
        lines.append(f"  {r['max_contracts']:>9.0f} {r['max_vol']:>7.1f} {r['max_yur']:>7.1f} {r['trades']:>7d} "
                     f"{r['wr']:>5.1f}% {r['net_pnl']:>+10.0f} {r['max_dd']:>7.2f}%")

    lines.append("")

# ── Summary comparison ──
lines.append("=" * 100)
lines.append("  SUMMARY COMPARISON: FLAT vs BEST FRACTIONAL")
lines.append("=" * 100)
lines.append(f"\n  {'Ticker':>6} {'Sizing':>16} {'MC':>3} {'MV':>5} {'MY':>5} {'Trades':>7} {'WR%':>6} {'Net PnL':>10} {'Max DD%':>8} {'vs FLAT':>10}")
lines.append(f"  {'-'*6:>6} {'-'*16:>16} {'-'*3:>3} {'-'*5:>5} {'-'*5:>5} {'-'*7:>7} {'-'*6:>6} {'-'*10:>10} {'-'*8:>8} {'-'*10:>10}")

for ticker in ticker_list:
    sub = df_res[df_res['ticker'] == ticker]
    flat_row = sub[sub['sizing'] == 'FLAT'].iloc[0]
    flat_pnl = flat_row['net_pnl']

    frac_best = sub[sub['sizing'] != 'FLAT'].sort_values('net_pnl', ascending=False).iloc[0]
    diff = frac_best['net_pnl'] - flat_pnl

    lines.append(f"  {ticker:>6} {'FLAT':>16} {'1':>3} {'-':>5} {'-':>5} {flat_row['trades']:>7d} "
                 f"{flat_row['wr']:>5.1f}% {flat_pnl:>+10.0f} {flat_row['max_dd']:>7.2f}% {'-':>10}")

    lines.append(f"  {ticker:>6} {'FRACTIONAL':>16} {frac_best['max_contracts']:>3.0f} {frac_best['max_vol']:>5.1f} {frac_best['max_yur']:>5.1f} "
                 f"{frac_best['trades']:>7d} {frac_best['wr']:>5.1f}% {frac_best['net_pnl']:>+10.0f} {frac_best['max_dd']:>7.2f}% {diff:>+10.0f}")

# ── All results ──
lines.append("\n" + "=" * 100)
lines.append("  ALL RESULTS (sorted by Net PnL)")
lines.append("=" * 100)
lines.append(f"\n  {'Ticker':>6} {'Sizing':>16} {'MC':>3} {'MV':>5} {'MY':>5} {'Trades':>7} {'WR%':>6} {'Net PnL':>10} {'Max DD%':>8}")
lines.append(f"  {'-'*6:>6} {'-'*16:>16} {'-'*3:>3} {'-'*5:>5} {'-'*5:>5} {'-'*7:>7} {'-'*6:>6} {'-'*10:>10} {'-'*8:>8}")

for _, r in df_res.sort_values('net_pnl', ascending=False).iterrows():
    mc = int(r['max_contracts'])
    mv = r['max_vol']
    my = r['max_yur']
    sizing = r['sizing']
    lines.append(f"  {r['ticker']:>6} {sizing:>16} {mc:>3d} {mv:>5.1f} {my:>5.1f} "
                 f"{r['trades']:>7d} {r['wr']:>5.1f}% {r['net_pnl']:>+10.0f} {r['max_dd']:>7.2f}%")

lines.append("\n" + "=" * 100)
lines.append("  END OF REPORT")
lines.append("=" * 100)

report = '\n'.join(lines)

with open(out_dir / 'results.txt', 'w') as f:
    f.write(report)

print(f"\n  Report saved to {out_dir / 'results.txt'}")
print("\n[5] Done.")
