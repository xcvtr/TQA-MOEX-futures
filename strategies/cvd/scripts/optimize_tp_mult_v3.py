import json, numpy as np, clickhouse_connect as cc, psycopg2

ch = cc.get_client(host='10.0.0.60', port=8123)
conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='user')
cur = conn.cursor()
cur.execute('SELECT ticker, l_tp_pct, l_sl_pct, s_tp_pct, s_sl_pct FROM futures.strategy_cvd_portfolio ORDER BY ticker')
pg = {r[0]: {'ltp':float(r[1]),'lsl':float(r[2]),'stp':float(r[3]),'ssl':float(r[4])} for r in cur.fetchall()}
cur.close(); conn.close()

P=20; L=12; Z=0.6
mults = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

# Pre-compute max_excursion for each ticker
ch = cc.get_client(host='10.0.0.60', port=8123)

best = {}
for base in sorted(pg.keys()):
    sids = ch.query(f"SELECT DISTINCT secid FROM moex.tradestats_fo WHERE secid LIKE '{base}%'").result_rows
    sl = ", ".join(f"'{r[0]}'" for r in sids)
    if not sl: continue
    df = ch.query_df(f"""SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
        argMax(pr_high,SYSTIME) as hi, argMax(pr_low,SYSTIME) as lo, argMax(pr_close,SYSTIME) as prc,
        sum(vol_b) as vb, sum(vol_s) as vs FROM moex.tradestats_fo
        WHERE secid IN ({sl}) AND SYSTIME >= '2024-10-01' GROUP BY bt ORDER BY bt""")
    n = len(df)
    if n < 200: continue
    prc = df['prc'].values.astype(float); hi = df['hi'].values.astype(float); lo = df['lo'].values.astype(float)
    cvd = df['vb'].values.astype(float) - df['vs'].values.astype(float)
    dcvd = np.diff(cvd, prepend=cvd[0]); z = np.zeros(n)
    for i in range(P, n):
        s = dcvd[i-P:i]
        if s.std() > 0: z[i] = (dcvd[i] - s.mean()) / s.std()
    v = ~(np.isnan(z) | np.isnan(prc))
    ml = np.where((z > Z) & v)[0]; ms = np.where((z < -Z) & v)[0]
    p = pg[base]

    for d, idx, tp_end, sl_p in [('L',ml,p['ltp'],p['lsl']),('S',ms,p['stp'],p['ssl'])]:
        if len(idx) < 10: continue

        # Compute max_excursion per signal
        max_exc_arr = np.zeros(len(idx))
        for k, i in enumerate(idx):
            if i+L >= n: continue
            if d == 'L':
                max_exc_arr[k] = hi[i+1:i+L+1].max() / prc[i] - 1
            else:
                max_exc_arr[k] = -(lo[i+1:i+L+1].min() / prc[i] - 1)
        max_p80 = np.percentile(max_exc_arr[max_exc_arr>0], 80) * 100 if np.any(max_exc_arr>0) else tp_end

        res = []
        for mult in mults:
            tp_pct = tp_end + (max_p80 - tp_end) * mult  # interpolate between end and max
            pnls = []
            for i in idx:
                if i+L>=n: continue
                entry = prc[i]
                if d=='L':
                    tp = entry * (1 + tp_pct/100)
                    sl = entry * (1 - sl_p/100)
                    for j in range(i+1, i+L+1):
                        if hi[j] >= tp: pnls.append(tp_pct/100); break
                        if lo[j] <= sl: pnls.append(-sl_p/100); break
                else:
                    tp = entry * (1 - tp_pct/100)
                    sl = entry * (1 + sl_p/100)
                    for j in range(i+1, i+L+1):
                        if lo[j] <= tp: pnls.append(tp_pct/100); break
                        if hi[j] >= sl: pnls.append(-sl_p/100); break
            if len(pnls) < 10: continue
            arr = np.array(pnls); eq = np.cumsum(arr)
            peak = np.maximum.accumulate(eq); dd = peak - eq; mdd = np.max(dd)
            res.append({'mult':mult, 'n':len(arr), 'tp':round(tp_pct,4),
                'wr':round(np.mean(arr>0)*100,1),
                'total':round(np.sum(arr)*100,2),
                'mdd':round(mdd*100,2),
                'calmar':round(np.sum(arr)/mdd,2) if mdd>0 else 0})

        if not res: continue
        best_r = max(res, key=lambda x: x['calmar'])
        best.setdefault(base, {})[d] = best_r
        print(f"{base:6s} {d}: mult={best_r['mult']:>4.2f} TP={best_r['tp']:>6.2f}% WR={best_r['wr']:>5.1f}% total={best_r['total']:>+8.2f}% DD={best_r['mdd']:>6.2f}% calmar={best_r['calmar']:>6.2f}")

print(f"\n{'Ticker':>6} | Dir | {'Mult':>5} | {'TP%':>6} | {'WR%':>5} | {'Total%':>8} | {'DD%':>6} | {'Calmar':>6}")
for t in sorted(best.keys()):
    for d in ['L','S']:
        if d in best[t]:
            r = best[t][d]
            print(f"{t:>6} | {d:>3} | {r['mult']:>5.2f} | {r['tp']:>6.2f} | {r['wr']:>5.1f} | {r['total']:>+8.2f} | {r['mdd']:>6.2f} | {r['calmar']:>6.2f}")
