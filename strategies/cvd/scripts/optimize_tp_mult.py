import json, numpy as np, clickhouse_connect as cc, psycopg2

ch = cc.get_client(host='10.0.0.60', port=8123)
conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='user')
cur = conn.cursor()
cur.execute("SELECT ticker, l_tp_pct, l_sl_pct, s_tp_pct, s_sl_pct FROM futures.strategy_cvd_portfolio ORDER BY ticker")
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
    best[base] = {'L':{},'S':{}}

    for d, idx, tp_b, sl_p in [('L',ml,p['ltp'],p['lsl']),('S',ms,p['stp'],p['ssl'])]:
        if len(idx) < 10:
            best[base][d] = {'mult':1.0,'wr':0,'p80':0,'net80':0}
            continue
        res = []
        for m in mults:
            tp_pct_val = tp_b * m
            pnls = []
            for i in idx:
                if i + L >= n: continue
                e = prc[i]
                if d == 'L':
                    tp = e * (1 + tp_pct_val/100); sl = e * (1 - sl_p/100)
                    hit_tp = hit_sl = False
                    for j in range(i+1, i+L+1):
                        if hi[j] >= tp: hit_tp = True; break
                        if lo[j] <= sl: hit_sl = True; break
                    if hit_tp: pnls.append(tp_pct_val/100)
                    elif hit_sl: pnls.append(-sl_p/100)
                else:
                    tp = e * (1 - tp_pct_val/100); sl = e * (1 + sl_p/100)
                    hit_tp = hit_sl = False
                    for j in range(i+1, i+L+1):
                        if lo[j] <= tp: hit_tp = True; break
                        if hi[j] >= sl: hit_sl = True; break
                    if hit_tp: pnls.append(tp_pct_val/100)
                    elif hit_sl: pnls.append(-sl_p/100)
            if len(pnls) < 10: continue
            arr = np.array(pnls) * 100
            wr = np.mean(arr > 0) * 100
            p80 = np.percentile(arr, 80)
            p20 = np.percentile(arr, 20)
            net80 = p80 - abs(p20)
            res.append({'mult':m,'wr':round(wr,1),'p80':round(p80,4),'net80':round(net80,4)})

        if res:
            best_m = max(res, key=lambda x: x['net80'])
            best[base][d] = best_m
            print(f"{base:6s} {d}: mult={best_m['mult']:>4.1f} WR={best_m['wr']:>5.1f}% net80={best_m['net80']:>+6.2f}% (tested {len(res)})")

conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='user')
cur = conn.cursor()
cur.execute("ALTER TABLE futures.strategy_cvd_portfolio ADD COLUMN IF NOT EXISTS l_tp_mult_opt NUMERIC(6,2) DEFAULT 1.0")
cur.execute("ALTER TABLE futures.strategy_cvd_portfolio ADD COLUMN IF NOT EXISTS s_tp_mult_opt NUMERIC(6,2) DEFAULT 1.0")

for base, b in best.items():
    lm = b['L'].get('mult',1.0); sm = b['S'].get('mult',1.0)
    cur.execute("UPDATE futures.strategy_cvd_portfolio SET l_tp_mult_opt=%s, s_tp_mult_opt=%s WHERE ticker=%s", (lm, sm, base))
conn.commit()

cur.execute("SELECT ticker, l_tp_pct, l_tp_mult_opt, s_tp_pct, s_tp_mult_opt FROM futures.strategy_cvd_portfolio ORDER BY ticker")
print(f"\n{'Ticker':>6} | {'L_tp%':>6} | {'L_mult':>6} | {'L_act%':>8} | {'S_tp%':>6} | {'S_mult':>6} | {'S_act%':>8}")
for r in cur.fetchall():
    print(f"{r[0]:>6} | {r[1]:>6.2f} | {r[2]:>6.1f} | {r[1]*r[2]:>+7.2f}% | {r[3]:>6.2f} | {r[4]:>6.1f} | {r[3]*r[4]:>+7.2f}%")
cur.close(); conn.close()
