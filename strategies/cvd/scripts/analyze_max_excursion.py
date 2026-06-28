import json, numpy as np, clickhouse_connect as cc
from datetime import datetime

ch = cc.get_client(host='10.0.0.60', port=8123)

PERIOD = 20
LOOKAHEAD = 12
Z = 0.6

tickers = ['FV','OZ','TI','AS','VI','DL','S0','PS','Si','FN','TN','SS','W4','WU','GZ','IP','RB','CR']

# Get all secids for our tickers
all_secids = {}
for t in tickers:
    rows = ch.query(f"SELECT DISTINCT secid FROM moex.tradestats_fo WHERE secid LIKE '{t}%'").result_rows
    all_secids[t] = [r[0] for r in rows]

results = []

for base in tickers:
    secid_list = ", ".join(f"'{s}'" for s in all_secids[base])
    if not secid_list:
        continue

    df = ch.query_df(f"""
        SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt,
               argMax(pr_open, SYSTIME) as opn,
               argMax(pr_high, SYSTIME) as hi,
               argMax(pr_low, SYSTIME) as lo,
               argMax(pr_close, SYSTIME) as prc,
               sum(vol_b) as vb, sum(vol_s) as vs
        FROM moex.tradestats_fo
        WHERE secid IN ({secid_list}) AND SYSTIME >= '2024-10-01'
        GROUP BY bt ORDER BY bt
    """)

    n = len(df)
    if n < 200:
        continue

    prc = df['prc'].values.astype(float)
    hi = df['hi'].values.astype(float)
    lo = df['lo'].values.astype(float)

    # CVD z-score
    cvd = df['vb'].values.astype(float) - df['vs'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0])
    dcvd_z = np.zeros(n)
    for i in range(PERIOD, n):
        s = dcvd[i-PERIOD:i]
        if s.std() > 0:
            dcvd_z[i] = (dcvd[i] - s.mean()) / s.std()

    valid = ~(np.isnan(dcvd_z) | np.isnan(prc))

    # MAX excursion for long: highest high in next LOOKAHEAD bars
    # MIN excursion for short: lowest low in next LOOKAHEAD bars
    max_fwd = np.zeros(n)
    min_fwd = np.zeros(n)
    end_fwd = np.zeros(n)

    for i in range(n - LOOKAHEAD):
        if not valid[i]:
            continue
        max_fwd[i] = hi[i+1:i+LOOKAHEAD+1].max() / prc[i] - 1
        min_fwd[i] = lo[i+1:i+LOOKAHEAD+1].min() / prc[i] - 1
        end_fwd[i] = prc[i+LOOKAHEAD] / prc[i] - 1

    mask_long = (dcvd_z > Z) & valid
    mask_short = (dcvd_z < -Z) & valid

    # Long signals
    ret_long = max_fwd[mask_long] * 100  # max excursion in % for long
    ret_long_end = end_fwd[mask_long] * 100  # end point for long
    ret_long_min = min_fwd[mask_long] * 100  # worst intra-window for long (for SL)

    # Short signals: favourable = price goes down
    ret_short = -min_fwd[mask_short] * 100  # max excursion in our favour for short
    ret_short_end = -end_fwd[mask_short] * 100
    ret_short_max = -max_fwd[mask_short] * 100  # worst intra-window for short

    def stats(arr):
        if len(arr) < 10: return {}
        return {
            'n': len(arr),
            'wr': round(np.mean(arr > 0) * 100, 1),
            'mean': round(np.mean(arr), 4),
            'median': round(np.median(arr), 4),
            'std': round(np.std(arr), 4),
            'p80': round(np.percentile(arr, 80), 4),
            'p20': round(np.percentile(arr, 20), 4),
            'w_mean': round(np.mean(arr[arr > 0]), 4) if np.any(arr > 0) else 0,
            'l_mean': round(np.mean(arr[arr < 0]), 4) if np.any(arr < 0) else 0,
        }

    r = {'ticker': base}

    # Long excursion stats
    ls = stats(ret_long)
    for k, v in ls.items(): r[f'L_max_{k}'] = v
    es = stats(ret_long_end)
    for k, v in es.items(): r[f'L_end_{k}'] = v
    ws = stats(ret_long_min)
    for k, v in ws.items(): r[f'L_min_{k}'] = v

    # Short excursion stats
    ss = stats(ret_short)
    for k, v in ss.items(): r[f'S_max_{k}'] = v
    es2 = stats(ret_short_end)
    for k, v in es2.items(): r[f'S_end_{k}'] = v
    ws2 = stats(ret_short_max)
    for k, v in ws2.items(): r[f'S_max_{k}'] = v

    results.append(r)
    print(f"{base}: L={r.get('L_max_n',0)} end={r.get('L_end_wr',0)}% max_p80={r.get('L_max_p80',0)}% end_p80={r.get('L_end_p80',0)}%")

# Save
data = {'params': {'PERIOD': PERIOD, 'LOOKAHEAD': LOOKAHEAD, 'Z': Z, 'tickers': tickers}, 'results': results}
with open('/home/user/projects/TQA-MOEX-futures/reports/cvd_max_excursion.json', 'w') as f:
    json.dump(data, f, indent=2)

print(f"\nSaved. Tickers: {len(results)}")
