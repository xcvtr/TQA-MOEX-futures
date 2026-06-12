#!/usr/bin/env python3
"""
Volume x OI — Variant 3: Exit by yur_z threshold.
Exit when yur_z drops below EXIT_YZ_THRESHOLD, adaptive.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import clickhouse_connect
import pandas as pd
import numpy as np
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# ── Config ──
TICKERS = ['PD', 'CC', 'IB']
DAYS = 400
since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')
COMMISSION = 2

PARAMS = {
    'PD': {'vol_z': 3.0, 'yur_z': 1.5, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'vol_z': 3.5, 'yur_z': 1.5, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'IB': {'vol_z': 3.5, 'yur_z': 2.0, 'sl_pct': 0.02, 'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}

ATR_FILTER = 1.0
EXIT_YZ_THRESHOLDS = [0.0, 0.5, 1.0]
MAX_HOLD_VALUES = [12, 24, 48]


def rolling_zs(vals, w=20):
    s = pd.Series(vals).ffill()
    mu = s.rolling(w, min_periods=w // 2).mean()
    sd = s.rolling(w, min_periods=w // 2).std().replace(0, 1)
    return ((s - mu) / sd).fillna(0)


def compute_atr(high, low, close, period=14):
    close_series = pd.Series(close)
    tr = pd.Series(np.maximum(
        high - low,
        np.maximum(
            np.abs(high - close_series.shift(1).values),
            np.abs(low - close_series.shift(1).values)
        )
    ))
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr.values


def calc_pnl_rub(direction, entry, exit_price, contracts, cfg):
    moves = (exit_price - entry) / cfg['minstep']
    if direction.upper() == 'SHORT':
        moves = -moves
    return moves * cfg['tick_rub'] * contracts


def max_dd_from_equity(equity):
    if not equity:
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
print("[1] Loading data...")
all_ohlcv = {}
all_signals = {}

for ticker in TICKERS:
    print(f"  {ticker}...", end=' ', flush=True)
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m AS p
        INNER JOIN moex.prices_5m_oi AS o ON o.symbol = p.symbol AND o.time = p.time
        WHERE p.symbol = %(t)s AND p.time >= %(s)s AND p.volume > 0 AND o.total_oi > 0
        ORDER BY p.time
    """, parameters={'t': ticker, 's': since}).result_rows

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

    all_ohlcv[ticker] = df

    p = PARAMS[ticker]
    mask = (df['vol_z'] > p['vol_z']) & (df['yur_z'] > p['yur_z']) & (df['fiz_z'] < 0)
    sig_idx = df[mask].index.tolist()

    sigs = []
    for idx in sig_idx:
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
        sigs.append({
            'entry_idx': entry_idx,
            'entry_open': entry_open,
        })
    all_signals[ticker] = sigs
    print(f"{len(sigs)} signals / {len(df):,} bars")

print()

# ── 2. Grid search over exit_yz x max_hold ──
print("[2] Running simulations...\n")

all_results = []

for exit_yz in EXIT_YZ_THRESHOLDS:
    for max_hold in MAX_HOLD_VALUES:
        label = f"EXIT_YZ<{exit_yz}  MAX_HOLD={max_hold}"
        print(f"{'=' * 80}")
        print(f"  {label}")
        print(f"{'=' * 80}")

        all_trades = []

        for ticker in TICKERS:
            df = all_ohlcv[ticker]
            cfg = PARAMS[ticker]
            sigs = all_signals[ticker]

            trades = []
            for sig in sigs:
                i = sig['entry_idx']
                entry = sig['entry_open']
                stop_price = entry * (1 - cfg['sl_pct'])
                max_idx = i + max_hold
                if max_idx >= len(df):
                    continue

                exit_price = None
                exit_reason = None
                exit_bar = None

                for j in range(i + 1, max_idx + 1):
                    current_yz = float(df.iloc[j]['yur_z'])

                    # 1. Exit when yur_z drops below threshold
                    if current_yz < exit_yz:
                        exit_price = float(df.iloc[j]['close'])
                        exit_reason = 'YZ_EXIT'
                        exit_bar = j
                        break

                    # 2. Max hold reached
                    bars_held = j - i
                    if bars_held >= max_hold:
                        exit_price = float(df.iloc[j]['close'])
                        exit_reason = 'MAX_HOLD'
                        exit_bar = j
                        break

                    # 3. Stop-loss
                    low_j = float(df.iloc[j]['low'])
                    if low_j <= stop_price:
                        exit_price = float(df.iloc[j]['close'])
                        exit_reason = 'STOP'
                        exit_bar = j
                        break

                if exit_price is None:
                    continue

                pnl = calc_pnl_rub('LONG', entry, exit_price, 1, cfg)
                comm = COMMISSION
                net_pnl = pnl - comm
                bars_held = exit_bar - i

                trades.append({
                    'ticker': ticker,
                    'net_pnl': net_pnl,
                    'pnl': pnl,
                })

            all_trades.extend(trades)

        # ── Results per ticker ──
        print(f"\n{'Ticker':>6} {'Trades':>7} {'WR%':>5} {'Net PnL':>10} {'Max DD%':>8} {'Avg Win':>9} {'Avg Loss':>9}")
        print("-" * 60)

        by_ticker = {}
        for t in all_trades:
            tk = t['ticker']
            by_ticker.setdefault(tk, []).append(t['net_pnl'])

        total_net = 0
        for tk in TICKERS:
            if tk not in by_ticker:
                print(f"{tk:>6} {'0':>7} {'N/A':>5} {'N/A':>10} {'N/A':>8} {'N/A':>9} {'N/A':>9}")
                continue
            pnls = by_ticker[tk]
            n = len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            wr = len(wins) / n * 100 if n else 0
            net = sum(pnls)
            total_net += net
            avg_win = np.mean(wins) if wins else 0
            avg_loss = np.mean(losses) if losses else 0

            eq = [PARAMS[tk]['go']]
            for p in pnls:
                eq.append(eq[-1] + p)
            mdd = max_dd_from_equity(eq) * 100

            print(f"{tk:>6} {n:>7} {wr:>5.1f} {net:>+10.0f} {mdd:>7.2f}% {avg_win:>+9.0f} {avg_loss:>+9.0f}")

        print(f"\n  Portfolio Total Net PnL: {total_net:+.0f} RUB")

        all_results.append({
            'exit_yz': exit_yz,
            'max_hold': max_hold,
            'total_net': total_net,
        })

# ── 3. Best combos ──
print(f"\n{'=' * 80}")
print("  TOP 3 COMBINATIONS (by Portfolio Net PnL)")
print(f"{'=' * 80}")
print(f"{'EXIT_YZ':>8} {'MAX_HOLD':>9} {'Net PnL':>10}")
print("-" * 30)

best = sorted(all_results, key=lambda r: r['total_net'], reverse=True)[:3]
for r in best:
    print(f"{r['exit_yz']:>8.1f} {r['max_hold']:>9d} {r['total_net']:>+10.0f}")

print("\n[3] Done.")
