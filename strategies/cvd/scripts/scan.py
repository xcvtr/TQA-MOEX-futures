#!/usr/bin/env python3
"""
Systematic correlation analysis: CVD signal strength vs available data.
Tests all combinations across tradestats_fo, prices_5m_oi, hi2_fo, alerts_fo.
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

# 1. Get all tickers from tradestats_fo
secids = [r[0] for r in client.query("SELECT DISTINCT secid FROM moex.tradestats_fo ORDER BY secid").result_rows]
from collections import OrderedDict
tm = OrderedDict()
for s in secids:
    b = s[:-2] if len(s) > 2 else s
    tm.setdefault(b, []).append(s)
all_bases = list(tm.keys())
print(f"Total bases: {len(all_bases)}", file=sys.stderr)

# 2. Get tickers in other tables
p5m_oi_syms = set(r[0] for r in client.query("SELECT DISTINCT symbol FROM moex.prices_5m_oi").result_rows)
hi2_secids = set(r[0] for r in client.query("SELECT DISTINCT secid FROM moex.hi2_fo").result_rows)
hi2_bases = set(s[:-3] if len(s) > 3 else s for s in hi2_secids)  # AFH0 -> AF
alert_secids = set(r[0] for r in client.query("SELECT DISTINCT secid FROM moex.alerts_fo").result_rows)
alert_bases = set(s[:-3] if len(s) > 3 else s for s in alert_secids)

print(f"prices_5m_oi: {len(p5m_oi_syms)}", file=sys.stderr)
print(f"hi2_fo bases: {len(hi2_bases)}", file=sys.stderr)
print(f"alerts_fo bases: {len(alert_bases)}", file=sys.stderr)

# Focus on tickers that have tradestats_fo data + at least one other source
focus = [t for t in all_bases if t in p5m_oi_syms or t in hi2_bases or t in alert_bases]
print(f"Focus tickers (have ts + at least one other): {len(focus)}", file=sys.stderr)

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
    except:
        continue
    if len(df_ts) < 200:
        continue
    
    # --- B. Get YUR data from prices_5m_oi ---
    df_oi = None
    if base in p5m_oi_syms:
        try:
            df_oi = client.query_df(f"""
                SELECT time as bt, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
                FROM moex.prices_5m_oi WHERE symbol='{base}' AND time>='2024-10-01' ORDER BY time
            """)
        except:
            pass
    
    # --- C. Get HHI data ---
    df_hi2 = None
    for s in hi2_secids:
        if s.startswith(base):
            try:
                df_hi2 = client.query_df(f"""
                    SELECT tradetime as bt, value
                    FROM moex.hi2_fo WHERE secid='{s}' AND tradedate>='2024-10-01' ORDER BY tradetime
                """)
                break
            except:
                continue
    
    # --- D. Get Alerts data ---
    df_al = None
    alert_count = 0
    for s in alert_secids:
        if s.startswith(base):
            try:
                df_al = client.query_df(f"""
                    SELECT tradetime as bt FROM moex.alerts_fo 
                    WHERE secid='{s}' AND tradedate>='2024-10-01' ORDER BY tradetime
                """)
                alert_count = len(df_al)
                break
            except:
                continue
    
    # Merge all together
    df_ts['bt'] = pd.to_datetime(df_ts['bt'])
    if df_oi is not None:
        df_oi['bt'] = pd.to_datetime(df_oi['bt'])
        df = pd.merge(df_ts, df_oi, on='bt', how='left', suffixes=('', '_oi'))
    else:
        df = df_ts.copy()
    
    if len(df) < 200:
        continue
    
    # Fill NaN OI with 0
    for col in ['fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype(float)
    
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
    
    # --- METRICS: OI ---
    oi_c = df['oi_c'].values.astype(float)
    oi_z = np.zeros(n)
    doi_z = np.zeros(n)
    doi = np.diff(oi_c, prepend=oi_c[0])
    for i in range(PERIOD, n):
        s = oi_c[i-PERIOD:i]
        if s.std() > 0: oi_z[i] = (oi_c[i] - s.mean()) / s.std()
        sd = doi[i-PERIOD:i]
        if sd.std() > 0: doi_z[i] = (doi[i] - sd.mean()) / sd.std()
    
    # --- METRICS: YUR_NET ---
    yur_z = np.zeros(n)
    dyur_z = np.zeros(n)
    fiz_z = np.zeros(n)
    if 'yur_buy' in df.columns:
        yur_net = df['yur_buy'].values - df['yur_sell'].values
        fiz_net = df['fiz_buy'].values - df['fiz_sell'].values
        dyur = np.diff(yur_net, prepend=yur_net[0])
        for i in range(PERIOD, n):
            s = yur_net[i-PERIOD:i]
            if s.std() > 0: yur_z[i] = (yur_net[i] - s.mean()) / s.std()
            sd = dyur[i-PERIOD:i]
            if sd.std() > 0: dyur_z[i] = (dyur[i] - sd.mean()) / sd.std()
            fs = fiz_net[i-PERIOD:i]
            if fs.std() > 0: fiz_z[i] = (fiz_net[i] - fs.mean()) / fs.std()
    
    # --- METRICS: DISB ---
    disb = df['disb'].values.astype(float)
    disb_z = np.zeros(n)
    for i in range(PERIOD, n):
        s = disb[i-PERIOD:i]
        if s.std() > 0: disb_z[i] = (disb[i] - s.mean()) / s.std()
    
    # --- Valid mask ---
    valid = ~(np.isnan(dcvd_z) | np.isnan(fwd))
    
    # --- Test each signal variant ---
    variants = {
        'CVD_only': (dcvd_z > Z) | (dcvd_z < -Z),
        'CVD+OI_level': ((dcvd_z > Z) & (oi_z > Z)) | ((dcvd_z < -Z) & (oi_z < -Z)),
        'CVD+OI_flow': ((dcvd_z > Z) & (doi_z > Z)) | ((dcvd_z < -Z) & (doi_z < -Z)),
        'CVD+YUR_level': ((dcvd_z > Z) & (yur_z > Z)) | ((dcvd_z < -Z) & (yur_z < -Z)),
        'CVD+YUR_flow': ((dcvd_z > Z) & (dyur_z > Z)) | ((dcvd_z < -Z) & (dyur_z < -Z)),
        'CVD+FIZ': ((dcvd_z > Z) & (fiz_z > Z)) | ((dcvd_z < -Z) & (fiz_z < -Z)),
        'CVD+DISB': ((dcvd_z > Z) & (disb_z > Z)) | ((dcvd_z < -Z) & (disb_z < -Z)),
        'CVD+OI_ANY': ((dcvd_z > Z) & ((oi_z > 0) | (yur_z > 0))) | ((dcvd_z < -Z) & ((oi_z < 0) | (yur_z < 0))),
    }
    
    ticker_results = {'ticker': base, 'alert_count': alert_count}
    
    for name, mask in variants.items():
        sig = fwd[valid & mask]
        sig = sig[~np.isnan(sig)]
        if len(sig) < 50:
            continue
        sig_ret_pct = sig * 100
        wr = np.mean(sig > 0) * 100
        p80 = np.percentile(sig_ret_pct, 80) if len(sig_ret_pct) >= 20 else 0
        net80 = p80
        mean_ret = np.mean(sig_ret_pct)
        
        ticker_results[f'{name}_n'] = len(sig)
        ticker_results[f'{name}_wr'] = round(wr, 1)
        ticker_results[f'{name}_net80'] = round(net80, 2)
        ticker_results[f'{name}_mean'] = round(mean_ret, 4)
    
    # Also compute CVD-only P80 (standard baseline)
    mask_long = (dcvd_z > Z) & valid
    mask_short = (dcvd_z < -Z) & valid
    ret_long = fwd[mask_long]
    ret_short = fwd[mask_short]
    ret_long = ret_long[~np.isnan(ret_long)]
    ret_short = ret_short[~np.isnan(ret_short)]
    if len(ret_long) >= 50 or len(ret_short) >= 50:
        all_ret = np.concatenate([ret_long, -ret_short]) * 100
        wr_cvd = np.mean(all_ret > 0) * 100
        p80_l = np.percentile(ret_long * 100, 80) if len(ret_long) >= 20 else 0
        p80_s = np.percentile(-ret_short * 100, 80) if len(ret_short) >= 20 else 0
        net80_cvd = (p80_l * len(ret_long) + p80_s * len(ret_short)) / max(len(ret_long)+len(ret_short), 1)
        ticker_results['CVD_n'] = len(ret_long) + len(ret_short)
        ticker_results['CVD_wr'] = round(wr_cvd, 1)
        ticker_results['CVD_net80'] = round(net80_cvd, 2)
        ticker_results['CVD_mean'] = round(np.mean(all_ret), 4)
    
    # Add data quality metrics
    ticker_results['has_yur'] = 1 if 'yur_buy' in df.columns else 0
    ticker_results['has_hi2'] = 1 if df_hi2 is not None else 0
    ticker_results['has_alert'] = 1 if df_al is not None else 0
    ticker_results['total_bars'] = n
    ticker_results['mean_oi'] = round(float(df['oi_c'].mean()), 0)
    ticker_results['std_oi'] = round(float(df['oi_c'].std()), 0)
    if 'total_oi' in df.columns:
        ticker_results['mean_total_oi'] = round(float(df['total_oi'].mean()), 0)
    
    results.append(ticker_results)
    
    if (idx+1) % 20 == 0:
        print(f"[{idx+1}/{len(focus)}] {base}", file=sys.stderr)
        sys.stderr.flush()

# Save
data = {'results': results}
p = f'/tmp/cvd_correlation_scan_{uuid.uuid4().hex[:4]}.json'
with open(p, 'w') as f:
    json.dump(data, f, indent=2)

print(f"Saved: {p}", file=sys.stderr)
print(f"Tickers analyzed: {len(results)}", file=sys.stderr)
