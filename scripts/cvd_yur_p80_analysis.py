#!/usr/bin/env python3
"""
P80 analysis: CVD + YUR_NET level confirmation (correct formula from checkpoint 099)
z(dCVD, 20) > 0.6  AND  z(yur_net, 20) > 0.6  for LONG
z(dCVD, 20) < -0.6 AND  z(yur_net, 20) < -0.6 for SHORT
"""
import clickhouse_connect
import pandas as pd
import numpy as np
import sys, json, uuid

CH_HOST = '10.0.0.60'
CH_DB = 'moex'
PERIOD = 20
LOOKAHEAD = 12
Z_THRESHOLD = 0.6

client = clickhouse_connect.get_client(host=CH_HOST, database=CH_DB)

r = client.query("SELECT DISTINCT secid FROM moex.tradestats_fo ORDER BY secid").result_rows
all_secids = [r[0] for r in r]
from collections import OrderedDict
tm = OrderedDict()
for s in all_secids:
    b = s[:-2] if len(s) > 2 else s
    tm.setdefault(b, []).append(s)

all_bases = list(tm.keys())
try:
    r = client.query("SELECT DISTINCT symbol FROM moex.prices_5m_oi ORDER BY symbol").result_rows
    oi_symbols = set(r[0] for r in r)
except:
    oi_symbols = set()

overlap = [t for t in all_bases if t in oi_symbols]
print(f"Overlap: {len(overlap)}/{len(all_bases)}", file=sys.stderr)

results_cvd = []
results_cvd_yur = []
results_yur_only = []

for idx, base in enumerate(overlap):
    secids = tm[base]
    sl = ", ".join(f"'{s}'" for s in secids)

    q1 = f"""
        SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
               argMax(pr_close, SYSTIME) as close,
               sum(vol_b) as vb, sum(vol_s) as vs,
               argMax(oi_close, SYSTIME) as oi
        FROM moex.tradestats_fo WHERE secid IN ({sl}) AND SYSTIME >= '2024-10-01'
        GROUP BY bt ORDER BY bt
    """
    try:
        d1 = client.query_df(q1)
    except Exception as e:
        continue
    if len(d1) < PERIOD + LOOKAHEAD + 5:
        continue

    q2 = f"""
        SELECT time as bt, yur_buy, yur_sell
        FROM moex.prices_5m_oi WHERE symbol = '{base}' AND time >= '2024-10-01'
        ORDER BY time
    """
    try:
        d2 = client.query_df(q2)
    except Exception as e:
        continue
    if len(d2) < PERIOD + LOOKAHEAD + 5:
        continue

    d1['bt'] = pd.to_datetime(d1['bt'])
    d2['bt'] = pd.to_datetime(d2['bt'])
    df = pd.merge(d1, d2, on='bt', how='inner')
    if len(df) < PERIOD + LOOKAHEAD + 5:
        continue

    # CVD change (flow)
    cvd = df['vb'].astype(float) - df['vs'].astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0])
    dcvd_ma = pd.Series(dcvd).rolling(PERIOD).mean().values
    dcvd_std = pd.Series(dcvd).rolling(PERIOD).std(ddof=0).values
    dcvd_z = np.where(dcvd_std > 0, (dcvd - dcvd_ma) / dcvd_std, 0)

    # YUR_NET LEVEL (CORRECT: level, not change)
    yur_net = df['yur_buy'].astype(float) - df['yur_sell'].astype(float)
    yur_net_ma = pd.Series(yur_net).rolling(PERIOD).mean().values
    yur_net_std = pd.Series(yur_net).rolling(PERIOD).std(ddof=0).values
    yur_net_z = np.where(yur_net_std > 0, (yur_net - yur_net_ma) / yur_net_std, 0)

    # Forward returns
    fwd = np.full(len(df), np.nan)
    if LOOKAHEAD < len(df):
        fwd[:-LOOKAHEAD] = df['close'].values[LOOKAHEAD:] / df['close'].values[:-LOOKAHEAD] - 1

    nanmask = ~(np.isnan(dcvd_z) | np.isnan(yur_net_z) | np.isnan(fwd))

    # CVD-only
    mc = nanmask & (dcvd_z > Z_THRESHOLD)
    mc_s = nanmask & (dcvd_z < -Z_THRESHOLD)
    sc = fwd[mc]
    sc_s = fwd[mc_s]

    # CVD + YUR_net level (CORRECT)
    mcy = nanmask & (dcvd_z > Z_THRESHOLD) & (yur_net_z > Z_THRESHOLD)
    mcy_s = nanmask & (dcvd_z < -Z_THRESHOLD) & (yur_net_z < -Z_THRESHOLD)
    scy = fwd[mcy]
    scy_s = fwd[mcy_s]

    # YUR-only level
    my = nanmask & (yur_net_z > Z_THRESHOLD)
    my_s = nanmask & (yur_net_z < -Z_THRESHOLD)
    sy = fwd[my]
    sy_s = fwd[my_s]

    def p80(rl, rs):
        if len(rl) < 100 and len(rs) < 100:
            return None
        a = np.concatenate([rl, -rs])
        if len(a) < 100:
            return None
        wr = float(np.mean(a > 0)) * 100
        p80u = float(np.percentile(rl, 80)) * 100 if len(rl) >= 20 else 0
        p80d = float(np.percentile(-rs, 80)) * 100 if len(rs) >= 20 else 0
        np80 = (p80u * len(rl) + p80d * len(rs)) / max(len(rl)+len(rs), 1)
        return {'sig_l': len(rl), 'sig_s': len(rs), 'sig_t': len(rl)+len(rs),
                'wr': round(wr, 1), 'net_p80': round(np80, 2)}

    r1 = p80(sc[~np.isnan(sc)], sc_s[~np.isnan(sc_s)])
    r2 = p80(scy[~np.isnan(scy)], scy_s[~np.isnan(scy_s)])
    r3 = p80(sy[~np.isnan(sy)], sy_s[~np.isnan(sy_s)])

    if r1: r1['ticker'] = base; results_cvd.append(r1)
    if r2: r2['ticker'] = base; results_cvd_yur.append(r2)
    if r3: r3['ticker'] = base; results_yur_only.append(r3)

    if (idx+1) % 10 == 0:
        print(f"[{idx+1}/{len(overlap)}] {base} C={len(sc) if r1 else 0} CY={len(scy) if r2 else 0}", file=sys.stderr)
        sys.stderr.flush()

data = {
    'config': {'period': PERIOD, 'lookahead': LOOKAHEAD, 'z': Z_THRESHOLD},
    'cvd': results_cvd, 'cvd_yur': results_cvd_yur, 'yur_only': results_yur_only
}
p = f'/tmp/p80_yur_level_{uuid.uuid4().hex[:4]}.json'
with open(p, 'w') as f:
    json.dump(data, f, indent=2)

print(f"SAVED:{p}", file=sys.stderr)
print(f"CVD: {len(results_cvd)}  CVD+YUR: {len(results_cvd_yur)}  YUR: {len(results_yur_only)}", file=sys.stderr)
