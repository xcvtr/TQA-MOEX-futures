import json, numpy as np, clickhouse_connect as cc, psycopg2

ch = cc.get_client(host='10.0.0.60', port=8123)
conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='user')
cur = conn.cursor()
cur.execute('SELECT ticker, l_tp_pct, l_sl_pct, s_tp_pct, s_sl_pct FROM futures.strategy_cvd_portfolio ORDER BY ticker')
pg = {r[0]: {'ltp':float(r[1]),'lsl':float(r[2]),'stp':float(r[3]),'ssl':float(r[4])} for r in cur.fetchall()}
cur.close(); conn.close()

P=20; L=12; Z=0.6
mults = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 20.0]

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

    for d, idx, tp_b, sl_p in [('L',ml,p['ltp'],p['lsl']),('S',ms,p['stp'],p['ssl'])]:
        if len(idx) < 10: continue
        res = []
        for m in mults:
            tp_pct_v = tp_b * m
            pnls = []
            for i in idx:
                if i+L>=n:
                    continue
                entry = prc[i]
                if d=='L':
                    tp = entry * (1 + tp_pct_v/100)
                    sl = entry * (1 - sl_p/100)
                    for j in range(i+1, i+L+1):
                        if hi[j] >= tp:
                            pnls.append(tp_pct_v/100)
                            break
                        if lo[j] <= sl:
                            pnls.append(-sl_p/100)
                            break
                else:
                    tp = entry * (1 - tp_pct_v/100)
                    sl = entry * (1 + sl_p/100)
                    for j in range(i+1, i+L+1):
                        if lo[j] <= tp:
                            pnls.append(tp_pct_v/100)
                            break
                        if hi[j] >= sl:
                            pnls.append(-sl_p/100)
                            break
            if len(pnls)<10: continue
            arr = np.array(pnls); eq = np.cumsum(arr); peak = np.maximum.accumulate(eq)
            dd = peak - eq; mdd = np.max(dd) if len(dd)>0 else 0
            total_ret = np.sum(arr)
            res.append({'mult':m, 'n':len(arr),
                'wr':round(np.mean(arr>0)*100,1),
                'mean':round(np.mean(arr)*100,4),
                'total':round(total_ret*100,2),
                'mdd':round(mdd*100,2),
                'calmar':round(total_ret/mdd,2) if mdd>0 else 0,
                'net80':round(np.percentile(arr*100,80)-abs(np.percentile(arr*100,20)),4)})

        if not res: continue
        best_by_total = max(res, key=lambda x: x['calmar'])
        best[base+'_'+d] = best_by_total
        print(f"{base:6s} {d}: mult={best_by_total['mult']:>4.1f} n={best_by_total['n']:>5} WR={best_by_total['wr']:>5.1f}% total={best_by_total['total']:>+8.2f}% DD={best_by_total['mdd']:>6.2f}% calmar={best_by_total['calmar']:>6.2f}")

# Save
conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='user')
cur = conn.cursor()
cur.execute("ALTER TABLE futures.strategy_cvd_portfolio ADD COLUMN IF NOT EXISTS l_mult_opt2 NUMERIC(6,2) DEFAULT 1.0")
cur.execute("ALTER TABLE futures.strategy_cvd_portfolio ADD COLUMN IF NOT EXISTS s_mult_opt2 NUMERIC(6,2) DEFAULT 1.0")

print(f"\n{'Ticker':>6} | Dir | {'Mult':>5} | {'N':>5} | {'WR%':>5} | {'Total%':>8} | {'DD%':>6} | {'Calmar':>6} | {'NetP80':>7}")
for k,v in sorted(best.items()):
    t,d = k.split('_')
    print(f"{t:>6} | {d:>3} | {v['mult']:>5.1f} | {v['n']:>5} | {v['wr']:>5.1f} | {v['total']:>+8.2f} | {v['mdd']:>6.2f} | {v['calmar']:>6.2f} | {v['net80']:>+7.2f}")
    if d=='L': cur.execute("UPDATE futures.strategy_cvd_portfolio SET l_mult_opt2=%s WHERE ticker=%s", (v['mult'], t))
    else: cur.execute("UPDATE futures.strategy_cvd_portfolio SET s_mult_opt2=%s WHERE ticker=%s", (v['mult'], t))

conn.commit(); cur.close(); conn.close()
print("\nSaved to PG (l_mult_opt2, s_mult_opt2)")
