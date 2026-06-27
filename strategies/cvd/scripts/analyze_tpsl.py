#!/usr/bin/env python3
"""
CVD P80 backtest with expanded TP/SL percentile statistics (P80/P20 for long & short).
Saves results to JSON and CSV, prints summary table.
"""
import clickhouse_connect, json, sys, os, uuid
import numpy as np
import pandas as pd
from collections import defaultdict

CH = dict(host='10.0.0.60', database='moex')
PERIOD = 20
LOOKAHEAD = 12
Z = 0.6

client = clickhouse_connect.get_client(**CH)

# 20 tickers from portfolio
TICKERS = ['FV', 'OZ', 'TI', 'AS', 'VI', 'DL', 'S0', 'PS', 'Si', 'FN',
           'TN', 'SS', 'W4', 'WU', 'GZ', 'IP', 'RB', 'LJ', 'GC', 'CR']

from collections import OrderedDict

# 1. Get all tickers from tradestats_fo
secids = [r[0] for r in client.query("SELECT DISTINCT secid FROM moex.tradestats_fo ORDER BY secid").result_rows]
tm = OrderedDict()
for s in secids:
    b = s[:-2] if len(s) > 2 else s
    tm.setdefault(b, []).append(s)
all_bases = list(tm.keys())

# Filter to our 20 tickers
focus = [t for t in TICKERS if t in tm]
found_extra = [t for t in TICKERS if t not in tm]
if found_extra:
    print(f"WARNING: tickers not found in tradestats_fo: {found_extra}", file=sys.stderr)
print(f"Found {len(focus)}/{len(TICKERS)} tickers", file=sys.stderr)

results = []

for idx, base in enumerate(focus):
    secid_list = ", ".join(f"'{s}'" for s in tm[base])

    # --- A. Get CVD + OI data from tradestats_fo ---
    q_ts = f"""
        SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
               argMax(pr_close, SYSTIME) as prc,
               sum(vol_b) as vb, sum(vol_s) as vs,
               argMax(oi_close, SYSTIME) as oi_c,
               argMax(disb, SYSTIME) as disb
        FROM moex.tradestats_fo WHERE secid IN ({secid_list}) AND SYSTIME >= '2024-10-01'
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

    # Compute CVD signal
    cvd = df['vb'].values.astype(float) - df['vs'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0])
    dcvd_z = np.zeros(n)
    for i in range(PERIOD, n):
        s = dcvd[i-PERIOD:i]
        if s.std() > 0:
            dcvd_z[i] = (dcvd[i] - s.mean()) / s.std()

    # Forward returns
    fwd = np.full(n, np.nan)
    if LOOKAHEAD < n:
        fwd[:-LOOKAHEAD] = df['prc'].values[LOOKAHEAD:] / df['prc'].values[:-LOOKAHEAD] - 1

    # Valid mask
    valid = ~(np.isnan(dcvd_z) | np.isnan(fwd))

    # ========== EXPANDED TP/SL STATS ==========
    mask_long = (dcvd_z > Z) & valid
    mask_short = (dcvd_z < -Z) & valid

    ret_long = fwd[mask_long]
    ret_short = fwd[mask_short]
    ret_long = ret_long[~np.isnan(ret_long)]
    ret_short = ret_short[~np.isnan(ret_short)]

    ticker_results = {'ticker': base}

    # --- Long stats ---
    if len(ret_long) >= 10:
        long_ret_pct = ret_long * 100
        long_win = long_ret_pct[long_ret_pct > 0]
        long_loss = long_ret_pct[long_ret_pct <= 0]
        ticker_results['L_n'] = len(ret_long)
        ticker_results['L_wr'] = round(np.mean(ret_long > 0) * 100, 1)
        ticker_results['L_mean'] = round(np.mean(long_ret_pct), 4)
        ticker_results['L_std'] = round(np.std(long_ret_pct), 4)
        ticker_results['L_P80_TP'] = round(np.percentile(long_ret_pct, 80), 4) if len(long_ret_pct) >= 5 else 0
        ticker_results['L_P20_SL'] = round(np.percentile(long_ret_pct, 20), 4) if len(long_ret_pct) >= 5 else 0
        ticker_results['L_W_mean'] = round(np.mean(long_win), 4) if len(long_win) > 0 else 0
        ticker_results['L_L_mean'] = round(np.mean(long_loss), 4) if len(long_loss) > 0 else 0
        ticker_results['L_W_n'] = len(long_win)
        ticker_results['L_L_n'] = len(long_loss)
    else:
        ticker_results['L_n'] = len(ret_long)
        for k in ['L_wr', 'L_mean', 'L_std', 'L_P80_TP', 'L_P20_SL', 'L_W_mean', 'L_L_mean', 'L_W_n', 'L_L_n']:
            ticker_results[k] = 0

    # --- Short stats (returns inverted: -ret) ---
    if len(ret_short) >= 10:
        short_ret_pct = -ret_short * 100  # inverted: positive means correct short
        short_win = short_ret_pct[short_ret_pct > 0]
        short_loss = short_ret_pct[short_ret_pct <= 0]
        ticker_results['S_n'] = len(ret_short)
        ticker_results['S_wr'] = round(np.mean(-ret_short > 0) * 100, 1)  # same as np.mean(ret_short < 0) * 100
        ticker_results['S_mean'] = round(np.mean(short_ret_pct), 4)
        ticker_results['S_std'] = round(np.std(short_ret_pct), 4)
        ticker_results['S_P80_TP'] = round(np.percentile(short_ret_pct, 80), 4) if len(short_ret_pct) >= 5 else 0
        ticker_results['S_P20_SL'] = round(np.percentile(short_ret_pct, 20), 4) if len(short_ret_pct) >= 5 else 0
        ticker_results['S_W_mean'] = round(np.mean(short_win), 4) if len(short_win) > 0 else 0
        ticker_results['S_L_mean'] = round(np.mean(short_loss), 4) if len(short_loss) > 0 else 0
        ticker_results['S_W_n'] = len(short_win)
        ticker_results['S_L_n'] = len(short_loss)
    else:
        ticker_results['S_n'] = len(ret_short)
        for k in ['S_wr', 'S_mean', 'S_std', 'S_P80_TP', 'S_P20_SL', 'S_W_mean', 'S_L_mean', 'S_W_n', 'S_L_n']:
            ticker_results[k] = 0

    # --- Combined stats (all_ret = long ret + inverted short ret) ---
    if len(ret_long) >= 10 or len(ret_short) >= 10:
        all_ret = np.concatenate([ret_long, -ret_short]) * 100
        ticker_results['A_n'] = len(all_ret)
        ticker_results['A_wr'] = round(np.mean(all_ret > 0) * 100, 1)
        ticker_results['A_mean'] = round(np.mean(all_ret), 4)
        ticker_results['A_median'] = round(np.median(all_ret), 4)
        ticker_results['A_std'] = round(np.std(all_ret), 4)
        ticker_results['A_P80'] = round(np.percentile(all_ret, 80), 4) if len(all_ret) >= 5 else 0
        ticker_results['A_P20'] = round(np.percentile(all_ret, 20), 4) if len(all_ret) >= 5 else 0
    else:
        ticker_results['A_n'] = 0
        for k in ['A_wr', 'A_mean', 'A_median', 'A_std', 'A_P80', 'A_P20']:
            ticker_results[k] = 0

    # Data quality
    ticker_results['total_bars'] = n
    ticker_results['mean_oi'] = round(float(df['oi_c'].mean()), 0)

    results.append(ticker_results)
    print(f"[{idx+1}/{len(focus)}] {base}  L={len(ret_long)} S={len(ret_short)}", file=sys.stderr)
    sys.stderr.flush()

# ========== SAVE RESULTS ==========
output_dir = '/home/user/projects/TQA-MOEX-futures/reports'
os.makedirs(output_dir, exist_ok=True)

json_path = os.path.join(output_dir, 'cvd_p80_tp_sl_results.json')
csv_path = os.path.join(output_dir, 'cvd_p80_tp_sl_results.csv')

data = {
    'params': {'PERIOD': PERIOD, 'LOOKAHEAD': LOOKAHEAD, 'Z': Z, 'tickers': TICKERS},
    'results': results
}
with open(json_path, 'w') as f:
    json.dump(data, f, indent=2)
print(f"Saved JSON: {json_path}", file=sys.stderr)

# CSV
if results:
    df_out = pd.DataFrame(results)
    df_out.to_csv(csv_path, index=False)
    print(f"Saved CSV: {csv_path}", file=sys.stderr)

print(f"Tickers analyzed: {len(results)}", file=sys.stderr)

# ========== PRINT SUMMARY TABLE ==========
print()
print("=" * 150)
print(f"{'Ticker':<6} {'Sig':>5} {'WR%':>5} {'Mean%':>7} {'P80(TP)%':>9} {'P20(SL)%':>9} "
      f"{'LW_mean%':>9} {'LL_mean%':>9} {'SW_mean%':>9} {'SL_mean%':>9} "
      f"{'A_WR%':>5} {'A_mean%':>7}")
print("-" * 150)

for r in results:
    t = r['ticker']
    sig = r.get('A_n', 0)
    wr = r.get('A_wr', 0)
    mean = r.get('A_mean', 0)
    p80 = r.get('A_P80', 0)
    p20 = r.get('A_P20', 0)
    lw = r.get('L_W_mean', 0)
    ll = r.get('L_L_mean', 0)
    sw = r.get('S_W_mean', 0)
    sl = r.get('S_L_mean', 0)
    print(f"{t:<6} {sig:>5} {wr:>5.1f} {mean:>7.4f} {p80:>9.4f} {p20:>9.4f} "
          f"{lw:>9.4f} {ll:>9.4f} {sw:>9.4f} {sl:>9.4f} "
          f"{r.get('A_wr', 0):>5.1f} {r.get('A_mean', 0):>7.4f}")

print("=" * 150)
print()

# Also print long/short breakdown
print("=" * 130)
print(f"{'Ticker':<6} {'L_n':>5} {'L_WR':>5} {'L_mean':>7} {'L_TP80':>8} {'L_SL20':>8} "
      f"{'S_n':>5} {'S_WR':>5} {'S_mean':>7} {'S_TP80':>8} {'S_SL20':>8}")
print("-" * 130)
for r in results:
    print(f"{r['ticker']:<6} {r.get('L_n',0):>5} {r.get('L_wr',0):>5.1f} {r.get('L_mean',0):>7.4f} "
          f"{r.get('L_P80_TP',0):>8.4f} {r.get('L_P20_SL',0):>8.4f} "
          f"{r.get('S_n',0):>5} {r.get('S_wr',0):>5.1f} {r.get('S_mean',0):>7.4f} "
          f"{r.get('S_P80_TP',0):>8.4f} {r.get('S_P20_SL',0):>8.4f}")
print("=" * 130)

# Summary stats across all tickers
print()
print("CROSS-TICKER AGGREGATES:")
all_aggs = ['A_n', 'A_wr', 'A_mean', 'A_P80', 'A_P20', 'A_std', 'A_median',
            'L_n', 'L_wr', 'L_mean', 'L_P80_TP', 'L_P20_SL', 'L_W_mean', 'L_L_mean',
            'S_n', 'S_wr', 'S_mean', 'S_P80_TP', 'S_P20_SL', 'S_W_mean', 'S_L_mean']
for agg in all_aggs:
    vals = [r.get(agg, 0) for r in results]
    mean_v = np.mean(vals)
    median_v = np.median(vals)
    print(f"  {agg:>15}: mean={mean_v:>10.4f}  median={median_v:>10.4f}")
