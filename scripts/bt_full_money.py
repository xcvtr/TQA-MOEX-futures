#!/usr/bin/env python3
"""Backtest: reinvest, PCT, risk management, max contract caps."""
import clickhouse_connect as cc, numpy as np, psycopg2
from collections import defaultdict
from strategies.stop_hunt.prod.engine import check_signal as sh_check

ch = cc.get_client(host='10.0.0.64', port=8123)
P = [('GAZR','GZ'),('Si','Si'),('ROSN','RN'),('GOLD','GD')]
PCT = {'GZ':1.0,'RN':1.0,'GD':1.0,'Si':0.01,'BR':0.001,'NG':0.0001}

pg = psycopg2.connect(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='')
cur = pg.cursor(); spe = {}
for _, t in P:
    cur.execute('SELECT step_price,min_step,lot_volume,go FROM futures.ticker_specs WHERE ticker=%s', (t,))
    r = cur.fetchone()
    if r: spe[t] = {'sp':float(r[0]or 1),'ms':float(r[1]or 0.01),'lot':int(r[2]or 1),'go':float(r[3]or 0)}
cur.close(); pg.close()

data = {}
for asset, tkr in P:
    df = ch.query_df(f"SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,argMax(pr_open,SYSTIME) as opn,argMax(pr_high,SYSTIME) as hi,argMax(pr_low,SYSTIME) as lo,argMax(pr_close,SYSTIME) as prc FROM moex.tradestats_fo WHERE asset_code='{asset}' AND SYSTIME>='2025-01-01' GROUP BY bt ORDER BY bt")
    if df.empty or len(df) < 1000: continue
    data[tkr] = df
if not data: print('No data'); exit()

ml = max(len(df) for df in data.values()); TC = 4; TO = 12; at = []; po = []
CAP = 200000.0; GO_DISCOUNT = 0.6; RISK = 0.02; MAX_CTR = 100
MAX_DD = 0.20; CONSECUTIVE_LOSS_CAP = 3; FREEZE_TRADES = 5
eq = CAP; peak = CAP; dd_pct = 0; cons_losses = 0; freeze_left = 0

def contract_count(tkr, eq):
    s = spe[tkr]; go = s['go'] * GO_DISCOUNT
    if go <= 0: return 0
    base = max(1, int(eq * RISK / go)) if go > 0 else 1
    return min(base, MAX_CTR)

for bi in range(50, ml):
    if dd_pct >= MAX_DD * 100: continue  # DD limit reached, stop trading
    for tkr, df in data.items():
        if bi >= len(df): continue
        if any(not p['cls'] and p['tk'] == tkr for p in po): continue
        if freeze_left > 0: continue
        cc = contract_count(tkr, eq)
        if cc <= 0: continue
        bd = {'prc': float(df['prc'].iloc[bi]), 'hi': float(df['hi'].iloc[bi]), 'lo': float(df['lo'].iloc[bi])}
        if bi >= 20: bd['lo_hist'] = list(df['lo'].iloc[bi-20:bi].values); bd['hi_hist'] = list(df['hi'].iloc[bi-20:bi].values)
        sig = sh_check(bd, tkr)
        if not sig: continue
        ni = bi + 1; ms = spe[tkr]['ms']
        if ni >= len(df): continue
        ep = float(df['opn'].iloc[ni]) + ms; ep = round(ep / ms) * ms
        po.append({'tk':tkr,'eb':ni,'ep':ep,'cls':False,'pnl':0,'tp':None,'act':False,'ebi':bi,'dir':sig.get('direction','?'),'c':cc})
    for p in po:
        if p['cls']: continue
        tkr = p['tk']; df = data[tkr]; cc2 = p['c']
        if bi >= len(df) or p['eb'] >= bi: continue
        hi = float(df['hi'].iloc[bi]); lo = float(df['lo'].iloc[bi])
        s = spe[tkr]; sp, ms, lot = s['sp'], s['ms'], s['lot']; pct = PCT[tkr]
        if bi - p['ebi'] >= TO:
            pnl_raw = (float(df['prc'].iloc[bi]) - p['ep']) / ms * sp * lot * pct * cc2 - TC * cc2
            p['pnl'] = pnl_raw; p['cls'] = True; at.append(p)
            if pnl_raw > 0: cons_losses = 0; freeze_left = 0
            else:
                cons_losses += 1
                if cons_losses >= CONSECUTIVE_LOSS_CAP: freeze_left = FREEZE_TRADES
            eq += pnl_raw
            if eq > peak: peak = eq
            else:
                dd_from_peak = (peak - eq) / peak * 100
                if dd_from_peak > dd_pct: dd_pct = dd_from_peak
            continue
        if not p['act']:
            if hi >= p['ep'] * 1.005: p['act'] = True; p['tp'] = hi * (1 - 0.003)
        elif hi >= p['tp'] / (1 - 0.003): p['tp'] = hi * (1 - 0.003)
        ex = None
        if p['act'] and lo <= p['tp']: ex = p['tp']
        elif lo <= p['ep'] * 0.993: ex = lo
        if not ex: continue
        pnl_raw = (ex - p['ep']) / ms * sp * lot * pct * cc2 - TC * cc2
        p['pnl'] = pnl_raw; p['cls'] = True; at.append(p)
        if pnl_raw > 0: cons_losses = 0; freeze_left = 0
        else:
            cons_losses += 1
            if cons_losses >= CONSECUTIVE_LOSS_CAP: freeze_left = FREEZE_TRADES
        eq += pnl_raw
        if eq > peak: peak = eq
        else:
            dd_from_peak = (peak - eq) / peak * 100
            if dd_from_peak > dd_pct: dd_pct = dd_from_peak
    if freeze_left > 0: freeze_left -= 1

pnls = np.array([t['pnl'] for t in at])
wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
wr = len(wins) / len(pnls) * 100 if len(pnls) > 0 else 0
pf = abs(sum(wins) / sum(losses)) if len(losses) > 0 and sum(losses) != 0 else 999
cagr = ((eq / CAP) ** (1 / 1.5) - 1) * 100 if eq > 0 else -100
mdd = max(0, (peak - min(eq, peak)) / peak * 100) if peak > 0 else 0

print(f'Capital: {CAP:.0f} -> {eq:.0f}')
print(f'Trades: {len(at)} CAGR: {cagr:.0f}% MDD: {mdd:.1f}% WR: {wr:.1f}% PF: {pf:.2f}')
if len(wins) > 0 and len(losses) > 0:
    print(f'Avg Win: {np.mean(wins):.0f} Avg Loss: {np.mean(losses):.0f}')
td = defaultdict(lambda: {'p':[],'n':0})
for t in at: td[t['tk']]['p'].append(t['pnl']); td[t['tk']]['n'] += 1
for tk, d in sorted(td.items()):
    sp = np.array(d['p']); sw = sp[sp > 0]; avg_c = int(np.mean([t2['c'] for t2 in at if t2['tk'] == tk]))
    print(f'{tk:>4s} AvgCtr={avg_c:>3} Trades={d["n"]:>5} PnL={sum(sp)/1000:>+10.0f}K WR={len(sw)/len(sp)*100:.1f}%')
sd = defaultdict(lambda: {'p':[],'n':0})
for t in at: sd[t['dir']]['p'].append(t['pnl']); sd[t['dir']]['n'] += 1
for d, d2 in sorted(sd.items()):
    sp = np.array(d2['p']); sw = sp[sp > 0]; w2 = len(sw)/len(sp)*100 if len(sp)>0 else 0
    p2 = abs(sum(sw)/sum(sp[sp<=0])) if len(sp[sp<=0])>0 and sum(sp[sp<=0]) != 0 else 999
    print(f'{d:>5s} Trades={d2["n"]:>5} PnL={sum(sp)/1000:>+10.0f}K WR={w2:.1f}% PF={p2:.2f}')
print(f'\nConfig: CAP={CAP:.0f} RISK={RISK:.0%} MAX_CTR={MAX_CTR} GO_DISCOUNT={GO_DISCOUNT:.0%} DD_LIMIT={MAX_DD:.0%} FREEZE={CONSECUTIVE_LOSS_CAP}L→{FREEZE_TRADES}T')
