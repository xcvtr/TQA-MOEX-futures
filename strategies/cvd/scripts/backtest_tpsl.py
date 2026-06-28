#!/usr/bin/env python3
"""
CVD Backtest with TP/SL tracking using multipliers from PG.
For each signal, tracks whether price hits TP or SL within LOOKAHEAD bars.
TP = l_tp_pct * l_tp_mult (long), s_tp_pct * s_tp_mult (short)
SL = l_sl_pct (long), s_sl_pct (short)

Output: per-ticker stats + JSON to reports/backtest_tpsl_results.json
"""
import clickhouse_connect, json, sys, os
import numpy as np
import pandas as pd
import psycopg2
from collections import OrderedDict

CH = dict(host='10.0.0.60', database='moex')
PERIOD = 20
LOOKAHEAD = 12
Z = 0.6

PG_DSN = dict(host='10.0.0.60', dbname='moex', user='user', port=5432)

client = clickhouse_connect.get_client(**CH)

# ── 1. Load TP/SL params from PG ────────────────────────────────────────
def load_portfolio_params():
    conn = psycopg2.connect(**PG_DSN)
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, l_tp_pct, l_tp_mult, l_sl_pct,
               s_tp_pct, s_tp_mult, s_sl_pct
        FROM futures.strategy_cvd_portfolio
        WHERE enabled = true
        ORDER BY ticker
    """)
    params = {}
    for row in cur.fetchall():
        t = row[0]
        params[t] = {
            'l_tp_pct': float(row[1]),
            'l_tp_mult': float(row[2]),
            'l_sl_pct': float(row[3]),
            's_tp_pct': float(row[4]),
            's_tp_mult': float(row[5]),
            's_sl_pct': float(row[6]),
        }
    cur.close()
    conn.close()
    return params

portfolio_params = load_portfolio_params()
TICKERS = list(portfolio_params.keys())
print(f"Loaded {len(TICKERS)} tickers from portfolio", file=sys.stderr)
for t, p in portfolio_params.items():
    print(f"  {t}: L_TP={p['l_tp_pct']}*{p['l_tp_mult']}={p['l_tp_pct']*p['l_tp_mult']:.4f}% L_SL={p['l_sl_pct']}%  "
          f"S_TP={p['s_tp_pct']}*{p['s_tp_mult']}={p['s_tp_pct']*p['s_tp_mult']:.4f}% S_SL={p['s_sl_pct']}%",
          file=sys.stderr)

# ── 2. Get all secids from tradestats_fo ─────────────────────────────────
secids_all = [r[0] for r in client.query(
    "SELECT DISTINCT secid FROM moex.tradestats_fo ORDER BY secid"
).result_rows]

tm = OrderedDict()
for s in secids_all:
    b = s[:-2] if len(s) > 2 else s
    tm.setdefault(b, []).append(s)
all_bases = list(tm.keys())

# Filter to our tickers
focus = [t for t in TICKERS if t in tm]
missing = [t for t in TICKERS if t not in tm]
if missing:
    print(f"WARNING: tickers not found in tradestats_fo: {missing}", file=sys.stderr)
print(f"Found {len(focus)}/{len(TICKERS)} tickers in tradestats_fo", file=sys.stderr)

# ── 3. Backtest each ticker ─────────────────────────────────────────────
results = []

for idx, base in enumerate(focus):
    p = portfolio_params[base]
    secid_list = ", ".join(f"'{s}'" for s in tm[base])

    # Load 5min OHLCV data with high/low for touch tracking
    q_ts = f"""
        SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
               argMax(pr_close, SYSTIME) as prc,
               max(pr_high) as high,
               min(pr_low) as low,
               sum(vol_b) as vb,
               sum(vol_s) as vs
        FROM moex.tradestats_fo
        WHERE secid IN ({secid_list}) AND SYSTIME >= '2024-10-01'
        GROUP BY bt ORDER BY bt
    """
    try:
        df_ts = client.query_df(q_ts)
    except Exception as e:
        print(f"  Query error for {base}: {e}", file=sys.stderr)
        continue

    if len(df_ts) < 200:
        print(f"  Skipping {base}: only {len(df_ts)} bars", file=sys.stderr)
        continue

    df = df_ts.copy()
    n = len(df)

    # Ensure numeric types
    for col in ['prc', 'high', 'low', 'vb', 'vs']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # ── CVD signal ──────────────────────────────────────────────────────
    cvd = df['vb'].values.astype(float) - df['vs'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0])
    dcvd_z = np.zeros(n)
    for i in range(PERIOD, n):
        s = dcvd[i - PERIOD:i]
        if s.std() > 0:
            dcvd_z[i] = (dcvd[i] - s.mean()) / s.std()

    # Forward returns (for reference)
    fwd = np.full(n, np.nan)
    if LOOKAHEAD < n:
        fwd[:-LOOKAHEAD] = df['prc'].values[LOOKAHEAD:] / df['prc'].values[:-LOOKAHEAD] - 1

    valid = ~(np.isnan(dcvd_z) | np.isnan(fwd))

    # ── Signal indices ──────────────────────────────────────────────────
    long_idx = np.where((dcvd_z > Z) & valid)[0]
    short_idx = np.where((dcvd_z < -Z) & valid)[0]

    prices = df['prc'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)

    # ── TP/SL levels ────────────────────────────────────────────────────
    # Long TP % = l_tp_pct * l_tp_mult
    # Long SL % = l_sl_pct
    long_tp_pct = p['l_tp_pct'] * p['l_tp_mult']  # in percent
    long_sl_pct = p['l_sl_pct']                    # in percent
    # Short TP % = s_tp_pct * s_tp_mult
    # Short SL % = s_sl_pct
    short_tp_pct = p['s_tp_pct'] * p['s_tp_mult']  # in percent
    short_sl_pct = p['s_sl_pct']                    # in percent

    trades = []  # list of dicts

    # ── Process Long signals ────────────────────────────────────────────
    for i in long_idx:
        if i >= n - 1:
            continue
        entry_price = prices[i]
        tp_price = entry_price * (1 + long_tp_pct / 100.0)
        sl_price = entry_price * (1 - long_sl_pct / 100.0)

        max_bar = min(i + LOOKAHEAD, n)
        hit = 'timeout'
        hit_idx = -1
        exit_price = prices[min(i + LOOKAHEAD, n - 1)]

        for j in range(i + 1, max_bar):
            bar_high = highs[j]
            bar_low = lows[j]

            if np.isnan(bar_high) or np.isnan(bar_low):
                continue

            # Check TP first (take profit)
            if bar_high >= tp_price:
                hit = 'tp'
                hit_idx = j
                exit_price = min(bar_high, tp_price)
                break
            # Check SL (stop loss)
            if bar_low <= sl_price:
                hit = 'sl'
                hit_idx = j
                exit_price = max(bar_low, sl_price)
                break

        if hit == 'timeout':
            exit_price = prices[min(i + LOOKAHEAD, n - 1)]

        ret = (exit_price / entry_price - 1) * 100  # percent return
        win = ret > 0

        trades.append({
            'ticker': base,
            'direction': 'L',
            'entry_idx': int(i),
            'exit_idx': int(hit_idx if hit_idx >= 0 else min(i + LOOKAHEAD, n - 1)),
            'entry_price': round(entry_price, 6),
            'exit_price': round(exit_price, 6),
            'ret_pct': round(ret, 4),
            'hit': hit,
            'win': win,
            'tp_pct': round(long_tp_pct, 4),
            'sl_pct': round(long_sl_pct, 4),
            'tp_price': round(tp_price, 6),
            'sl_price': round(sl_price, 6),
        })

    # ── Process Short signals ───────────────────────────────────────────
    for i in short_idx:
        if i >= n - 1:
            continue
        entry_price = prices[i]
        # Short TP = price goes DOWN by short_tp_pct%
        tp_price = entry_price * (1 - short_tp_pct / 100.0)
        # Short SL = price goes UP by short_sl_pct%
        sl_price = entry_price * (1 + short_sl_pct / 100.0)

        max_bar = min(i + LOOKAHEAD, n)
        hit = 'timeout'
        hit_idx = -1

        for j in range(i + 1, max_bar):
            bar_high = highs[j]
            bar_low = lows[j]

            if np.isnan(bar_high) or np.isnan(bar_low):
                continue

            # Check TP first (price drops to TP)
            if bar_low <= tp_price:
                hit = 'tp'
                hit_idx = j
                exit_price = max(bar_low, tp_price)
                break
            # Check SL (price rises to SL)
            if bar_high >= sl_price:
                hit = 'sl'
                hit_idx = j
                exit_price = min(bar_high, sl_price)
                break

        if hit == 'timeout':
            exit_price = prices[min(i + LOOKAHEAD, n - 1)]

        ret = (entry_price / exit_price - 1) * 100  # positive = correct short
        win = ret > 0

        trades.append({
            'ticker': base,
            'direction': 'S',
            'entry_idx': int(i),
            'exit_idx': int(hit_idx if hit_idx >= 0 else min(i + LOOKAHEAD, n - 1)),
            'entry_price': round(entry_price, 6),
            'exit_price': round(exit_price, 6),
            'ret_pct': round(ret, 4),
            'hit': hit,
            'win': win,
            'tp_pct': round(short_tp_pct, 4),
            'sl_pct': round(short_sl_pct, 4),
            'tp_price': round(tp_price, 6),
            'sl_price': round(sl_price, 6),
        })

    # ── Aggregate per ticker ────────────────────────────────────────────
    if not trades:
        print(f"  [{idx+1}/{len(focus)}] {base}: NO trades", file=sys.stderr)
        continue

    df_trades = pd.DataFrame(trades)
    n_total = len(df_trades)
    n_long = len(df_trades[df_trades['direction'] == 'L'])
    n_short = len(df_trades[df_trades['direction'] == 'S'])
    wr_all = df_trades['win'].mean() * 100
    mean_ret = df_trades['ret_pct'].mean()
    net_pnl = df_trades['ret_pct'].sum()

    # TP/SL split
    tp_trades = df_trades[df_trades['hit'] == 'tp']
    sl_trades = df_trades[df_trades['hit'] == 'sl']
    timeout_trades = df_trades[df_trades['hit'] == 'timeout']
    n_tp = len(tp_trades)
    n_sl = len(sl_trades)
    n_to = len(timeout_trades)

    wr_tp = tp_trades['win'].mean() * 100 if n_tp > 0 else 0
    wr_sl = sl_trades['win'].mean() * 100 if n_sl > 0 else 0
    wr_to = timeout_trades['win'].mean() * 100 if n_to > 0 else 0

    # NetP80: sort by ret_pct, take top 20% net
    sorted_ret = np.sort(df_trades['ret_pct'].values)
    p80_idx = int(len(sorted_ret) * 0.8)
    net80 = sorted_ret[p80_idx:].sum() if p80_idx < len(sorted_ret) else 0

    # Long stats
    lt = df_trades[df_trades['direction'] == 'L']
    st = df_trades[df_trades['direction'] == 'S']
    wr_l = lt['win'].mean() * 100 if n_long > 0 else 0
    wr_s = st['win'].mean() * 100 if n_short > 0 else 0
    mean_l = lt['ret_pct'].mean() if n_long > 0 else 0
    mean_s = st['ret_pct'].mean() if n_short > 0 else 0

    ticker_res = {
        'ticker': base,
        'total_trades': n_total,
        'long_trades': n_long,
        'short_trades': n_short,
        'wr_pct': round(wr_all, 1),
        'mean_ret_pct': round(mean_ret, 4),
        'net_pnl_sum_pct': round(net_pnl, 4),
        'net80_pct': round(net80, 4),
        'tp_hits': n_tp,
        'sl_hits': n_sl,
        'timeout_exits': n_to,
        'tp_wr_pct': round(wr_tp, 1),
        'sl_wr_pct': round(wr_sl, 1),
        'timeout_wr_pct': round(wr_to, 1),
        'long_wr_pct': round(wr_l, 1),
        'short_wr_pct': round(wr_s, 1),
        'long_mean_ret_pct': round(mean_l, 4),
        'short_mean_ret_pct': round(mean_s, 4),
        'tp_pct_used': round(long_tp_pct, 4),
        'sl_pct_used': round(long_sl_pct, 4),
        'short_tp_pct_used': round(short_tp_pct, 4),
        'short_sl_pct_used': round(short_sl_pct, 4),
        'l_tp_mult': p['l_tp_mult'],
        's_tp_mult': p['s_tp_mult'],
        'total_bars': n,
    }
    results.append(ticker_res)
    print(f"  [{idx+1}/{len(focus)}] {base}: {n_total} trades, WR={wr_all:.1f}%, "
          f"TP={n_tp}/{wr_tp:.0f}% SL={n_sl}/{wr_sl:.0f}% TO={n_to}/{wr_to:.0f}%",
          file=sys.stderr)
    sys.stderr.flush()

# ── 4. Save results ─────────────────────────────────────────────────────
output_dir = '/home/user/projects/TQA-MOEX-futures/reports'
os.makedirs(output_dir, exist_ok=True)

json_path = os.path.join(output_dir, 'backtest_tpsl_results.json')

data = {
    'params': {
        'PERIOD': PERIOD,
        'LOOKAHEAD': LOOKAHEAD,
        'Z': Z,
        'tickers': TICKERS,
        'description': 'TP = pct * mult, SL = pct (no mult). Tracked within LOOKAHEAD bars.',
    },
    'results': results,
}
with open(json_path, 'w') as f:
    json.dump(data, f, indent=2)
print(f"Saved JSON: {json_path}", file=sys.stderr)

# CSV
if results:
    df_out = pd.DataFrame(results)
    csv_path = os.path.join(output_dir, 'backtest_tpsl_results.csv')
    df_out.to_csv(csv_path, index=False)
    print(f"Saved CSV: {csv_path}", file=sys.stderr)

print(f"Tickers analyzed: {len(results)}", file=sys.stderr)

# ── 5. Print summary table ─────────────────────────────────────────────
print()
print("=" * 160)
print(f"{'Ticker':<6} {'Sig':>5} {'WR%':>6} {'Mean%':>8} {'Net80%':>8} "
      f"{'TP':>4} {'TP_WR':>6} {'SL':>4} {'SL_WR':>6} {'TO':>4} {'TO_WR':>6} "
      f"{'L_WR':>5} {'S_WR':>5} {'TP_pct':>7} {'SL_pct':>7}")
print("-" * 160)

for r in results:
    print(f"{r['ticker']:<6} {r['total_trades']:>5} {r['wr_pct']:>6.1f} {r['mean_ret_pct']:>8.4f} {r['net80_pct']:>8.4f} "
          f"{r['tp_hits']:>4} {r['tp_wr_pct']:>6.1f} {r['sl_hits']:>4} {r['sl_wr_pct']:>6.1f} "
          f"{r['timeout_exits']:>4} {r['timeout_wr_pct']:>6.1f} "
          f"{r['long_wr_pct']:>5.1f} {r['short_wr_pct']:>5.1f} "
          f"{r['tp_pct_used']:>7.4f} {r['sl_pct_used']:>7.4f}")

print("=" * 160)
print()

# Per-direction breakdown
print("=" * 100)
print(f"{'Ticker':<6} {'L_n':>5} {'L_WR':>5} {'L_mean':>8} {'S_n':>5} {'S_WR':>5} {'S_mean':>8}")
print("-" * 100)
for r in results:
    print(f"{r['ticker']:<6} {r['long_trades']:>5} {r['long_wr_pct']:>5.1f} {r['long_mean_ret_pct']:>8.4f} "
          f"{r['short_trades']:>5} {r['short_wr_pct']:>5.1f} {r['short_mean_ret_pct']:>8.4f}")
print("=" * 100)
print()

# ── 6. Cross-ticker aggregates ──────────────────────────────────────────
print("CROSS-TICKER AGGREGATES:")
agg_keys = ['total_trades', 'wr_pct', 'mean_ret_pct', 'net80_pct',
            'tp_hits', 'sl_hits', 'timeout_exits', 'tp_wr_pct', 'sl_wr_pct', 'timeout_wr_pct',
            'long_wr_pct', 'short_wr_pct', 'long_mean_ret_pct', 'short_mean_ret_pct']
for k in agg_keys:
    vals = [r.get(k, 0) for r in results]
    if k in ('total_trades', 'tp_hits', 'sl_hits', 'timeout_exits'):
        print(f"  {k:>20}: sum={sum(vals):>10.0f}  mean={np.mean(vals):>10.2f}")
    else:
        print(f"  {k:>20}: mean={np.mean(vals):>10.4f}  median={np.median(vals):>10.4f}")

# TP hit breakdown
total_tp = sum(r['tp_hits'] for r in results)
total_sl = sum(r['sl_hits'] for r in results)
total_to = sum(r['timeout_exits'] for r in results)
total_all = total_tp + total_sl + total_to
print(f"\nEXIT BREAKDOWN (across all tickers):")
print(f"  TP hits:    {total_tp:>6} ({total_tp/total_all*100:>5.1f}%)" if total_all > 0 else "  TP hits:    0")
print(f"  SL hits:    {total_sl:>6} ({total_sl/total_all*100:>5.1f}%)" if total_all > 0 else "  SL hits:    0")
print(f"  Timeouts:   {total_to:>6} ({total_to/total_all*100:>5.1f}%)" if total_all > 0 else "  Timeouts:   0")

print("\nDone.")
