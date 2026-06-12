#!/usr/bin/env python3
"""
Volume x OI — Variant 4: FINAL.
Combines ATR-filter + adaptive yur_z exit + grid search over 8 tickers.
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

TICKERS = ['GD', 'MG', 'SN', 'GL', 'VB', 'PD', 'CC', 'IB']
DAYS = 400
SINCE = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')
COMMISSION = 2
EXIT_YZ = 0.5
STOP_LOSS = 0.02

VOL_Z_VALS = [2.5, 3.0, 3.5]
YUR_Z_VALS = [1.0, 1.5, 2.0]
MAX_HOLD_VALS = [12, 24, 48]
ATR_VALS = [0.5, 1.0, 1.5, 2.0, None]

CFG = {'minstep': 0.01, 'tick_rub': 1.0, 'go': 5000}


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


def atr_label(v):
    if v is None:
        return "no_filt"
    return f"{v}%"


# ── 1. Load data ───────────────────────────────────────────────
print("=" * 80)
print("  VOLUME x OI — VARIANT 4: FINAL GRID SEARCH")
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

# ── 2. Grid search ─────────────────────────────────────────────
n_combo = len(VOL_Z_VALS) * len(YUR_Z_VALS) * len(MAX_HOLD_VALS) * len(ATR_VALS)
total = len(ticker_list) * n_combo
print(f"\n[2] Grid search: {len(VOL_Z_VALS)}x{len(YUR_Z_VALS)}x{len(MAX_HOLD_VALS)}x{len(ATR_VALS)} = {n_combo} combos/ticker")
print(f"    Total combinations: {total}")
print()

results = []
combo_cnt = 0

for ticker in ticker_list:
    df = all_data[ticker]
    n_total = len(df)

    for vol_z_th in VOL_Z_VALS:
        for yur_z_th in YUR_Z_VALS:
            # Signal detection: vol_z > TH AND yur_z > TH AND fiz_z < 0
            mask = (df['vol_z'] > vol_z_th) & (df['yur_z'] > yur_z_th) & (df['fiz_z'] < 0)
            sig_indices_str = df[mask].index.tolist()

            for atr_th in ATR_VALS:
                for max_hold_val in MAX_HOLD_VALS:
                    combo_cnt += 1
                    if combo_cnt % 270 == 0:
                        pct = 100 * combo_cnt // total
                        print(f"    Progress: {combo_cnt}/{total} ({pct}%)")

                    trades = []

                    for idx in sig_indices_str:
                        # ATR filter on signal bar
                        if atr_th is not None:
                            atr_val = float(df.iloc[idx]['atr_pct'])
                            if atr_val > atr_th:
                                continue

                        # Entry at next bar open
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
                        exit_bar = None

                        for j in range(entry_idx + 1, max_exit_idx + 1):
                            current_yz = float(df.iloc[j]['yur_z'])

                            # 1. Exit when yur_z drops below threshold
                            if current_yz < EXIT_YZ:
                                exit_price = float(df.iloc[j]['close'])
                                exit_bar = j
                                break

                            # 2. Max hold reached
                            bars_held = j - entry_idx
                            if bars_held >= max_hold_val:
                                exit_price = float(df.iloc[j]['close'])
                                exit_bar = j
                                break

                            # 3. Stop-loss by low
                            low_j = float(df.iloc[j]['low'])
                            if low_j <= stop_price:
                                exit_price = float(df.iloc[j]['close'])
                                exit_bar = j
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

                    results.append({
                        'ticker': ticker,
                        'vol_z': vol_z_th,
                        'yur_z': yur_z_th,
                        'atr': atr_th if atr_th is not None else 0,
                        'atr_lbl': atr_label(atr_th),
                        'hold': max_hold_val,
                        'trades': n_trades,
                        'wr': wr,
                        'net_pnl': net,
                        'max_dd': mdd,
                    })

print(f"\n    Done. {combo_cnt} combinations processed.")

# ── 3. Analysis ────────────────────────────────────────────────
print(f"\n[3] Analysing results...")

df_res = pd.DataFrame(results)

# Top-10 by Net PnL
top10 = df_res.sort_values('net_pnl', ascending=False).head(10)

# Best ticker — which ticker has highest sum of net_pnl across all its combos
ticker_total = df_res.groupby('ticker')['net_pnl'].sum().sort_values(ascending=False)
best_ticker = ticker_total.index[0]

# Best ticker's best combo
best_ticker_combo = df_res[df_res['ticker'] == best_ticker].sort_values('net_pnl', ascending=False).iloc[0]

# Best unified combination — same params across all tickers
param_cols = ['vol_z', 'yur_z', 'atr', 'hold']
unified = df_res.groupby(param_cols, as_index=False).agg(
    total_pnl=('net_pnl', 'sum'),
    total_trades=('trades', 'sum'),
    combos=('ticker', 'count')
)
# Keep only combos that have entries for ALL tickers
unified = unified[unified['combos'] == len(ticker_list)].copy()
if len(unified) > 0:
    best_unified = unified.sort_values('total_pnl', ascending=False).iloc[0]
    best_unified_params = {c: best_unified[c] for c in param_cols}
else:
    best_unified = None
    best_unified_params = None

# Top-10 unified
top10_unified = unified.sort_values('total_pnl', ascending=False).head(10) if len(unified) > 0 else None


# ── 4. Save report ─────────────────────────────────────────────
print(f"\n[4] Saving report...")

out_dir = Path('reports/volume_oi_v4')
out_dir.mkdir(parents=True, exist_ok=True)

lines = []
lines.append("=" * 90)
lines.append("  VOLUME x OI — VARIANT 4: FINAL GRID SEARCH — RESULTS")
lines.append("=" * 90)
lines.append(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append(f"Tickers: {', '.join(ticker_list)}")
lines.append(f"Data window: {DAYS} days (since {SINCE})")
lines.append(f"Commission: {COMMISSION} RUB/contract")
lines.append(f"Exit yur_z threshold: {EXIT_YZ}")
lines.append(f"Stop-loss: {STOP_LOSS*100:.0f}%")
lines.append(f"Position: 1 contract (flat sizing)")
lines.append(f"Total combos tested: {combo_cnt}")
lines.append("")

# ── Top-10 by Net PnL ──
lines.append("-" * 90)
lines.append("  TOP-10 COMBINATIONS by Net PnL")
lines.append("-" * 90)
lines.append(f"{'Ticker':>6} {'vol_z':>6} {'yur_z':>6} {'atr_filter':>10} {'hold':>5} {'Trades':>7} {'WR%':>6} {'Net PnL':>10} {'Max DD%':>8}")
lines.append("-" * 90)
for _, r in top10.iterrows():
    atr_s = r['atr_lbl']
    lines.append(f"{r['ticker']:>6} {r['vol_z']:>6.1f} {r['yur_z']:>6.1f} {atr_s:>10} {r['hold']:>5d} {r['trades']:>7d} {r['wr']:>5.1f}% {r['net_pnl']:>+10.0f} {r['max_dd']:>7.2f}%")
lines.append("")

# ── Best ticker ──
lines.append("-" * 90)
lines.append("  BEST TICKER OVERALL (cumulative Net PnL across all combos)")
lines.append("-" * 90)
lines.append(f"\n  Best ticker: {best_ticker}")
lines.append(f"  Cumulative PnL: {ticker_total.max():+.0f} RUB")
lines.append(f"\n  Per-ticker cumulative PnL:")
for tk, pnl in ticker_total.items():
    lines.append(f"    {tk:>4}: {pnl:+10.0f} RUB")
lines.append("")

# ── Best ticker top-5 combos ──
best_tk_top5 = df_res[df_res['ticker'] == best_ticker].sort_values('net_pnl', ascending=False).head(5)
lines.append(f"  Top-5 combos for {best_ticker}:")
lines.append(f"  {'vol_z':>6} {'yur_z':>6} {'atr':>10} {'hold':>5} {'Trades':>7} {'WR%':>6} {'Net PnL':>10} {'Max DD%':>8}")
for _, r in best_tk_top5.iterrows():
    lines.append(f"  {r['vol_z']:>6.1f} {r['yur_z']:>6.1f} {r['atr_lbl']:>10} {r['hold']:>5d} {r['trades']:>7d} {r['wr']:>5.1f}% {r['net_pnl']:>+10.0f} {r['max_dd']:>7.2f}%")
lines.append("")

# ── Best unified combination ──
lines.append("-" * 90)
lines.append("  BEST UNIFIED COMBINATION (same params across ALL tickers)")
lines.append("-" * 90)

if best_unified is not None:
    bu = best_unified
    atr_s = atr_label(bu['atr'] if bu['atr'] != 0 else None)
    lines.append(f"\n  Params: vol_z={bu['vol_z']:.1f}  yur_z={bu['yur_z']:.1f}  "
                 f"atr={atr_s}  hold={int(bu['hold'])}")
    lines.append(f"  Total PnL: {bu['total_pnl']:+10.0f} RUB")
    lines.append(f"  Total trades: {int(bu['total_trades'])}")
    lines.append("")

    # Per-ticker breakdown for best unified
    lines.append(f"  Per-ticker breakdown for best unified combo:")
    lines.append(f"  {'Ticker':>6} {'Trades':>7} {'WR%':>6} {'Net PnL':>10} {'Max DD%':>8}")
    for tk in ticker_list:
        row = df_res[(df_res['ticker'] == tk) &
                     (df_res['vol_z'] == bu['vol_z']) &
                     (df_res['yur_z'] == bu['yur_z']) &
                     (df_res['atr'] == bu['atr']) &
                     (df_res['hold'] == bu['hold'])]
        if len(row) > 0:
            r = row.iloc[0]
            lines.append(f"  {tk:>6} {r['trades']:>7d} {r['wr']:>5.1f}% {r['net_pnl']:>+10.0f} {r['max_dd']:>7.2f}%")
    lines.append("")

    # Top-5 unified
    lines.append(f"  Top-5 unified combinations:")
    lines.append(f"  {'vol_z':>6} {'yur_z':>6} {'atr':>10} {'hold':>5} {'Trades':>7} {'Net PnL':>10}")
    for _, r in top10_unified.head(5).iterrows():
        atr_s = atr_label(r['atr'] if r['atr'] != 0 else None)
        lines.append(f"  {r['vol_z']:>6.1f} {r['yur_z']:>6.1f} {atr_s:>10} {r['hold']:>5.0f} {r['total_trades']:>7.0f} {r['total_pnl']:>+10.0f}")
else:
    lines.append("\n  No unified combination found (all tickers have data for all combo params?)")
lines.append("")

# ── Best per-ticker combo ──
lines.append("-" * 90)
lines.append("  BEST COMBO PER TICKER")
lines.append("-" * 90)
lines.append(f"  {'Ticker':>6} {'vol_z':>6} {'yur_z':>6} {'atr':>10} {'hold':>5} {'Trades':>7} {'WR%':>6} {'Net PnL':>10} {'Max DD%':>8}")
for tk in ticker_list:
    best_tk = df_res[df_res['ticker'] == tk].sort_values('net_pnl', ascending=False).iloc[0]
    lines.append(f"  {best_tk['ticker']:>6} {best_tk['vol_z']:>6.1f} {best_tk['yur_z']:>6.1f} "
                 f"{best_tk['atr_lbl']:>10} {best_tk['hold']:>5d} {best_tk['trades']:>7d} "
                 f"{best_tk['wr']:>5.1f}% {best_tk['net_pnl']:>+10.0f} {best_tk['max_dd']:>7.2f}%")
lines.append("")

# ── Summary per ticker (all combos) ──
lines.append("-" * 90)
lines.append("  SUMMARY PER TICKER (aggregated across all combos)")
lines.append("-" * 90)
lines.append(f"  {'Ticker':>6} {'Combos':>7} {'Avg PnL':>10} {'Best PnL':>10} {'Worst PnL':>10} {'Avg WR%':>7} {'Avg DD%':>8}")
for tk in ticker_list:
    sub = df_res[df_res['ticker'] == tk]
    lines.append(f"  {tk:>6} {len(sub):>7d} {sub['net_pnl'].mean():>+10.0f} {sub['net_pnl'].max():>+10.0f} "
                 f"{sub['net_pnl'].min():>+10.0f} {sub['wr'].mean():>6.1f}% {sub['max_dd'].mean():>7.2f}%")
lines.append("")

# ── New tickers analysis (GD, MG, SN, GL, VB) ──
new_tickers = [t for t in ticker_list if t in ['GD', 'MG', 'SN', 'GL', 'VB']]
if new_tickers:
    lines.append("-" * 90)
    lines.append("  NEW TICKERS ANALYSIS (GD, MG, SN, GL, VB)")
    lines.append("-" * 90)
    for tk in new_tickers:
        sub = df_res[df_res['ticker'] == tk].sort_values('net_pnl', ascending=False)
        best3 = sub.head(3)
        lines.append(f"\n  {tk}: best combo net_pnl={sub['net_pnl'].max():+.0f}, "
                     f"avg net_pnl={sub['net_pnl'].mean():+.0f}")
        for _, r in best3.iterrows():
            lines.append(f"    vol_z={r['vol_z']:.1f} yur_z={r['yur_z']:.1f} "
                         f"atr={r['atr_lbl']} hold={r['hold']} "
                         f"pnl={r['net_pnl']:+5.0f} wr={r['wr']:.0f}% dd={r['max_dd']:.1f}%")
    lines.append("")

lines.append("=" * 90)
lines.append("  END OF REPORT")
lines.append("=" * 90)

report = '\n'.join(lines)

with open(out_dir / 'results.txt', 'w') as f:
    f.write(report)

print(f"\n  Report saved to {out_dir / 'results.txt'}")
print("\n[5] Done.")
