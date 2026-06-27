import json, numpy as np, clickhouse_connect as cc

ch = cc.get_client(host='10.0.0.60', port=8123)

PERIOD = 20; LOOKAHEAD = 12; Z = 0.6
tickers = ['FV','OZ','TI','AS','VI','DL','S0','PS','Si','FN','TN','SS','W4','WU','GZ','IP','RB','CR']

all_secids = {}
for t in tickers:
    rows = ch.query(f"SELECT DISTINCT secid FROM moex.tradestats_fo WHERE secid LIKE '{t}%'").result_rows
    all_secids[t] = [r[0] for r in rows]

results = []
for base in tickers:
    secid_list = ", ".join(f"'{s}'" for s in all_secids[base])
    if not secid_list: continue
    df = ch.query_df(f"""SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
        argMax(pr_high,SYSTIME) as hi, argMax(pr_low,SYSTIME) as lo, argMax(pr_close,SYSTIME) as prc,
        sum(vol_b) as vb, sum(vol_s) as vs FROM moex.tradestats_fo
        WHERE secid IN ({secid_list}) AND SYSTIME >= '2024-10-01' GROUP BY bt ORDER BY bt""")
    n = len(df)
    if n < 200: continue
    prc = df['prc'].values.astype(float); hi = df['hi'].values.astype(float); lo = df['lo'].values.astype(float)
    cvd = df['vb'].values.astype(float) - df['vs'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0]); dcvd_z = np.zeros(n)
    for i in range(PERIOD, n):
        s = dcvd[i-PERIOD:i]
        if s.std() > 0: dcvd_z[i] = (dcvd[i] - s.mean()) / s.std()
    valid = ~(np.isnan(dcvd_z) | np.isnan(prc))

    # For each bar: compute max and min excursion in next LOOKAHEAD bars
    max_fwd = np.zeros(n); min_fwd = np.zeros(n)
    for i in range(n - LOOKAHEAD):
        if valid[i]:
            max_fwd[i] = hi[i+1:i+LOOKAHEAD+1].max() / prc[i] - 1
            min_fwd[i] = lo[i+1:i+LOOKAHEAD+1].min() / prc[i] - 1

    def stats(arr):
        if len(arr) < 10: return {}
        return {'n': len(arr), 'wr': round(np.mean(arr>0)*100,1),
            'mean': round(np.mean(arr),4), 'p80': round(np.percentile(arr,80),4),
            'p20': round(np.percentile(arr,20),4),
            'w_mean': round(np.mean(arr[arr>0]),4) if np.any(arr>0) else 0,
            'l_mean': round(np.mean(arr[arr<0]),4) if np.any(arr<0) else 0}

    mask_long = (dcvd_z > Z) & valid; mask_short = (dcvd_z < -Z) & valid
    r = {'ticker': base}

    # Long: favourable = max_fwd (up move), adverse = min_fwd (down move)
    fl = stats(max_fwd[mask_long] * 100)
    for k, v in fl.items(): r[f'L_fav_{k}'] = v
    al = stats(min_fwd[mask_long] * 100)
    for k, v in al.items(): r[f'L_adv_{k}'] = v

    # Short: favourable = -min_fwd (down move), adverse = -max_fwd (up move)
    fs = stats(-min_fwd[mask_short] * 100)
    for k, v in fs.items(): r[f'S_fav_{k}'] = v
    a_s = stats(-max_fwd[mask_short] * 100)
    for k, v in a_s.items(): r[f'S_adv_{k}'] = v

    results.append(r)
    print(f"{base}: L_fav_p80={r.get('L_fav_p80',0):>6.2f}% S_fav_p80={r.get('S_fav_p80',0):>6.2f}%")

# Save
with open('/home/user/projects/TQA-MOEX-futures/reports/cvd_max_excursion_v2.json', 'w') as f:
    json.dump({'params':{'PERIOD':PERIOD,'LOOKAHEAD':LOOKAHEAD,'Z':Z},'results':results}, f, indent=2)

print(f"\nSaved. Tickers: {len(results)}")

# Compute multipliers vs old P80 end
print(f"\n{'Ticker':>6} | {'L_end_P80':>8} | {'L_fav_P80':>8} | {'L_mult':>6} | {'S_end_P80':>8} | {'S_fav_P80':>8} | {'S_mult':>6}")
print('-'*65)
with open('/home/user/projects/TQA-MOEX-futures/reports/cvd_p80_tp_sl_results.json') as f:
    old = {r['ticker']: r for r in json.load(f)['results']}
for r in results:
    t = r['ticker']
    le = old[t]['L_P80_TP']; lf = r.get('L_fav_p80',0.001)
    se = old[t]['S_P80_TP']; sf = r.get('S_fav_p80',0.001)
    lm = round(lf/le,1) if le > 0.01 else 1
    sm = round(sf/se,1) if se > 0.01 else 1
    print(f"{t:>6} | {le:>8.2f} | {lf:>8.2f} | {lm:>6.1f} | {se:>8.2f} | {sf:>8.2f} | {sm:>6.1f}")
