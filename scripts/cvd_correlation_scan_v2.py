#!/usr/bin/env python3
"""
Optimized correlation scan: pre-compute ticker mappings, batch queries.
"""
import clickhouse_connect, json, os, sys, uuid
import numpy as np
import pandas as pd
from collections import defaultdict

CH = dict(host='10.0.0.60', database='moex')
PERIOD = 20; LOOKAHEAD = 12; Z_TH = 0.6

client = clickhouse_connect.get_client(**CH)

# Pre-compute all ticker mappings
print("Phase 1: Pre-computing mappings...", file=sys.stderr)

# tradestats_fo bases
rows = client.query("SELECT DISTINCT secid FROM moex.tradestats_fo ORDER BY secid").result_rows
all_secids = [r[0] for r in rows]
from collections import OrderedDict
tm = OrderedDict()
for s in all_secids:
    b = s[:-2] if len(s) > 2 else s
    tm.setdefault(b, []).append(s)
all_bases = list(tm.keys())
print(f"  tradestats_fo bases: {len(all_bases)}", file=sys.stderr)

# prices_5m_oi symbols
p5m_oi = set(r[0] for r in client.query("SELECT DISTINCT symbol FROM moex.prices_5m_oi").result_rows)
print(f"  prices_5m_oi: {len(p5m_oi)}", file=sys.stderr)

# Pre-compute hi2_fo base-2-secid mapping
hi2_all = client.query("SELECT DISTINCT secid FROM moex.hi2_fo").result_rows
hi2_map = defaultdict(list)
for (s,) in hi2_all:
    b = s[:-3] if len(s) > 3 else s
    hi2_map[b].append(s)
print(f"  hi2_fo bases: {len(hi2_map)}", file=sys.stderr)

# Pre-compute alerts_fo base-2-secid mapping
alert_all = client.query("SELECT DISTINCT secid FROM moex.alerts_fo").result_rows
alert_map = defaultdict(list)
for (s,) in alert_all:
    b = s[:-3] if len(s) > 3 else s
    alert_map[b].append(s)
print(f"  alerts_fo bases: {len(alert_map)}", file=sys.stderr)

# Focus: tickers that have tradestats_fo + at least one other
focus = [t for t in all_bases if t in p5m_oi or t in hi2_map or t in alert_map]
print(f"Focus: {len(focus)} tickers", file=sys.stderr)

# Pre-fetch alert counts per base
alert_counts = {}
for base in focus:
    for s in alert_map.get(base, []):
        r = client.query(f"SELECT count() FROM moex.alerts_fo WHERE secid='{s}'").result_rows
        alert_counts[base] = r[0][0]
        break

results = []

for idx, base in enumerate(focus):
    sid_list = ", ".join(f"'{s}'" for s in tm[base])
    
    # Fetch tradestats_fo data — batch per ticker
    df = client.query_df(f"""
        SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
               argMax(pr_close, SYSTIME) as prc,
               sum(vol_b) as vb, sum(vol_s) as vs,
               argMax(oi_close, SYSTIME) as oi_c,
               argMax(disb, SYSTIME) as disb
        FROM moex.tradestats_fo WHERE secid IN ({sid_list}) AND SYSTIME >= '2024-10-01'
        GROUP BY bt ORDER BY bt
    """)
    
    if len(df) < 200:
        continue
    
    n = len(df)
    
    # Merge YUR data if available
    if base in p5m_oi:
        try:
            df_oi = client.query_df(f"""
                SELECT time as bt, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
                FROM moex.prices_5m_oi WHERE symbol='{base}' AND time>='2024-10-01' ORDER BY time
            """)
            if len(df_oi) > 0:
                df_oi['bt'] = pd.to_datetime(df_oi['bt'])
                df['bt'] = pd.to_datetime(df['bt'])
                df = pd.merge(df, df_oi, on='bt', how='left', suffixes=('', '_oi'))
                for col in ['fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']:
                    if col in df.columns:
                        df[col] = df[col].fillna(0).astype(float)
        except:
            pass
    
    n = len(df)
    if n < 200:
        continue
    
    # VECTORIZED z-score computation using numpy
    # Much faster than pandas rolling.apply
    def rolling_z(arr, window):
        """Vectorized z-score using np.convolve (fast)."""
        n = len(arr)
        z = np.zeros(n)
        if n <= window:
            return z
        weights = np.ones(window) / window
        rolling_mean = np.convolve(arr, weights, mode='valid')
        # Pad to match original length
        mean_full = np.zeros(n)
        mean_full[window-1:] = rolling_mean
        mean_full[:window-1] = arr[:window-1]
        
        # Rolling std: sqrt(E[X²] - E[X]²)
        rolling_mean_sq = np.convolve(arr**2, weights, mode='valid')
        rolling_var = rolling_mean_sq - rolling_mean**2
        rolling_std_full = np.zeros(n)
        rolling_std_full[window-1:] = np.sqrt(np.maximum(rolling_var, 1e-10))
        rolling_std_full[:window-1] = 1.0
        
        # z-score at position i uses mean/std of PREVIOUS window (no look-ahead)
        # So z[i] depends on mean of arr[i-window:i]
        z[window:] = (arr[window:] - mean_full[window-1:-1]) / rolling_std_full[window-1:-1]
        return z

    n = len(df)
    
    cvd = df['vb'].values.astype(float) - df['vs'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0])
    prc = df['prc'].values.astype(float)
    dcvd_z = rolling_z(dcvd, PERIOD)
    
    oi_c = df['oi_c'].values.astype(float)
    oi_z = rolling_z(oi_c, PERIOD)
    doi = np.diff(oi_c, prepend=oi_c[0])
    doi_z = rolling_z(doi, PERIOD)
    
    # YUR/FIZ
    yur_z = np.zeros(n); dyur_z = np.zeros(n); fiz_z = np.zeros(n)
    if 'yur_buy' in df.columns:
        ynet = df['yur_buy'].values - df['yur_sell'].values
        fnet = df['fiz_buy'].values - df['fiz_sell'].values
        yur_z = rolling_z(ynet, PERIOD)
        dyn = np.diff(ynet, prepend=ynet[0])
        dyur_z = rolling_z(dyn, PERIOD)
        fiz_z = rolling_z(fnet, PERIOD)
    
    disb = df['disb'].values.astype(float)
    disb_z = rolling_z(disb, PERIOD)
    
    # Forward returns
    fwd = np.full(n, np.nan)
    if LOOKAHEAD < n:
        fwd[:-LOOKAHEAD] = prc[LOOKAHEAD:] / prc[:-LOOKAHEAD] - 1
    
    # Valid mask (no NaNs)
    valid = ~(np.isnan(dcvd_z) | np.isnan(fwd) | np.isnan(oi_z))
    
    def p80(ret): return np.percentile(ret * 100, 80) if len(ret) >= 20 else 0
    
    tr = {'ticker': base, 'n_bars': n, 'alert_cnt': alert_counts.get(base, 0),
          'has_yur': 1 if 'yur_buy' in df.columns else 0,
          'has_hi2': 1 if base in hi2_map else 0}
    tr['mean_oi'] = round(float(df['oi_c'].mean()), 0)
    tr['cv_oi'] = round(float(df['oi_c'].std() / max(df['oi_c'].mean(), 1)), 3)
    tr['cv_yur'] = 0
    if 'yur_buy' in df.columns:
        y = df['yur_buy'].values - df['yur_sell'].values
        tr['cv_yur'] = round(float(y.std() / max(abs(y.mean()), 1)), 3)
    
    # CVD-only baseline (both long + short, with P80)
    ml = valid & (dcvd_z > Z_TH)
    ms = valid & (dcvd_z < -Z_TH)
    rl = fwd[ml]; rs = fwd[ms]
    rl = rl[~np.isnan(rl)]; rs = rs[~np.isnan(rs)]
    
    # Test: CVD-only
    sig_names = {
        'CVD': (valid & ((dcvd_z > Z_TH) | (dcvd_z < -Z_TH)), None),
    }
    
    if 'yur_buy' in df.columns:
        sig_names['CVD+OI_LVL'] = (valid & ((dcvd_z > Z_TH) & (oi_z > Z_TH)) | ((dcvd_z < -Z_TH) & (oi_z < -Z_TH)), None)
        sig_names['CVD+OI_FLW'] = (valid & ((dcvd_z > Z_TH) & (doi_z > Z_TH)) | ((dcvd_z < -Z_TH) & (doi_z < -Z_TH)), None)
        sig_names['CVD+YUR_LVL'] = (valid & ((dcvd_z > Z_TH) & (yur_z > Z_TH)) | ((dcvd_z < -Z_TH) & (yur_z < -Z_TH)), None)
        sig_names['CVD+YUR_FLW'] = (valid & ((dcvd_z > Z_TH) & (dyur_z > Z_TH)) | ((dcvd_z < -Z_TH) & (dyur_z < -Z_TH)), None)
        sig_names['CVD+FIZ'] = (valid & ((dcvd_z > Z_TH) & (fiz_z > Z_TH)) | ((dcvd_z < -Z_TH) & (fiz_z < -Z_TH)), None)
        sig_names['CVD+DISB'] = (valid & ((dcvd_z > Z_TH) & (disb_z > Z_TH)) | ((dcvd_z < -Z_TH) & (disb_z < -Z_TH)), None)
        
        # Combined: CVD + ANY of [OI, YUR, DISB]
        any_confirm = (oi_z > Z_TH) | (yur_z > Z_TH) | (disb_z > Z_TH)
        any_refute = (oi_z < -Z_TH) | (yur_z < -Z_TH) | (disb_z < -Z_TH)
        sig_names['CVD+ANY'] = (valid & ((dcvd_z > Z_TH) & any_confirm) | ((dcvd_z < -Z_TH) & any_refute), None)
        
        # Relaxed: lower threshold
        sig_names['CVD+YUR_LVL_relax'] = (valid & ((dcvd_z > Z_TH) & (yur_z > 0.3)) | ((dcvd_z < -Z_TH) & (yur_z < -0.3)), None)
    else:
        sig_names['CVD+OI_LVL'] = (valid & ((dcvd_z > Z_TH) & (oi_z > Z_TH)) | ((dcvd_z < -Z_TH) & (oi_z < -Z_TH)), None)
        sig_names['CVD+OI_FLW'] = (valid & ((dcvd_z > Z_TH) & (doi_z > Z_TH)) | ((dcvd_z < -Z_TH) & (doi_z < -Z_TH)), None)
        sig_names['CVD+DISB'] = (valid & ((dcvd_z > Z_TH) & (disb_z > Z_TH)) | ((dcvd_z < -Z_TH) & (disb_z < -Z_TH)), None)
        sig_names['CVD+ANY'] = (valid & ((dcvd_z > Z_TH) & (oi_z > Z_TH)) | ((dcvd_z < -Z_TH) & (oi_z < -Z_TH)), None)
    
    for name, (mask, _) in sig_names.items():
        sig = fwd[mask]
        sig = sig[~np.isnan(sig)]
        if len(sig) < 50:
            continue
        wr = np.mean(sig > 0) * 100
        net80 = p80(sig) if name != 'CVD' else 0
        tr[f'{name}_n'] = len(sig)
        tr[f'{name}_wr'] = round(wr, 1)
        tr[f'{name}_net80'] = round(net80, 2)
    
    # CVD P80 (long+short properly)
    if len(rl) + len(rs) >= 50:
        all_ret = np.concatenate([rl, -rs]) * 100
        tr['CVD_wr'] = round(np.mean(all_ret > 0) * 100, 1)
        p80l = p80(rl); p80s = p80(-rs)
        tr['CVD_net80'] = round((p80l * len(rl) + p80s * len(rs)) / max(len(rl)+len(rs), 1), 2)
        tr['CVD_n'] = len(rl) + len(rs)
    
    results.append(tr)
    if (idx+1) % 20 == 0:
        print(f"[{idx+1}/{len(focus)}] {base}", file=sys.stderr)
        sys.stderr.flush()

# Save
out = {'results': results}
p = f'/tmp/cvd_corr_scan_{uuid.uuid4().hex[:4]}.json'
with open(p, 'w') as f:
    json.dump(out, f, indent=2)
print(f"SAVED:{p}", file=sys.stderr)
print(f"Total: {len(results)}", file=sys.stderr)
