#!/usr/bin/env python3
"""Portfolio backtest: Stop Hunt, 5 tickers, SHORT+LONG combined, no lot (step_price per contract)."""
import clickhouse_connect as cc, numpy as np, psycopg2
from collections import defaultdict
from strategies.stop_hunt.prod.engine import check_signal as sh_check

ch = cc.get_client(host='10.0.0.64', port=8123)
P = [('GAZR','GZ'),('Si','Si'),('ROSN','RN'),('GOLD','GD'),('CNYRUBF','CR')]

pg = psycopg2.connect(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='')
cur = pg.cursor(); spe = {}
for _, t in P:
    cur.execute('SELECT step_price,min_step FROM futures.ticker_specs WHERE ticker=%s', (t,))
    r = cur.fetchone()
    if r: spe[t] = {'sp': float(r[0] or 1), 'ms': float(r[1] or 0.01)}
cur.close(); pg.close()

data = {}
for asset, tkr in P:
    q = ("SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,"
         "argMax(pr_open,SYSTIME) as opn,"
         "argMax(pr_high,SYSTIME) as hi,"
         "argMax(pr_low,SYSTIME) as lo,"
         "argMax(pr_close,SYSTIME) as prc "
         f"FROM moex.tradestats_fo WHERE asset_code='{asset}' "
         "AND SYSTIME>='2025-01-01' GROUP BY bt ORDER BY bt")
    df = ch.query_df(q)
    if df.empty or len(df) < 1000: continue
    data[tkr] = df
    print(f'{tkr} {asset} {len(df)} bars', flush=True)

if not data:
    print('ERROR: No data loaded'); exit(1)

ml = max(len(df) for df in data.values())
TC = 4; TO = 12; at = []; po = []

for bi in range(50, ml):
    for tkr, df in data.items():
        if bi >= len(df): continue
        if any(not p['cls'] and p['tk'] == tkr for p in po): continue
        ms = spe[tkr]['ms']
        bd = {'prc': float(df['prc'].iloc[bi]), 'hi': float(df['hi'].iloc[bi]),
              'lo': float(df['lo'].iloc[bi])}
        if bi >= 20:
            bd['lo_hist'] = list(df['lo'].iloc[bi - 20:bi].values)
            bd['hi_hist'] = list(df['hi'].iloc[bi - 20:bi].values)
        sig = sh_check(bd, tkr)
        if not sig: continue
        ni = bi + 1
        if ni >= len(df): continue
        ep = float(df['opn'].iloc[ni]) + ms
        ep = round(ep / ms) * ms
        po.append({'tk': tkr, 'eb': ni, 'ep': ep, 'cls': False,
                   'pnl': 0, 'tp': None, 'act': False, 'ebi': bi,
                   'dir': sig.get('direction', '?')})
    for p in po:
        if p['cls']: continue
        tkr = p['tk']; df = data[tkr]
        if bi >= len(df) or p['eb'] >= bi: continue
        hi = float(df['hi'].iloc[bi]); lo = float(df['lo'].iloc[bi])
        s = spe[tkr]; sp, ms = s['sp'], s['ms']
        if bi - p['ebi'] >= TO:
            p['pnl'] = (float(df['prc'].iloc[bi]) - p['ep']) / ms * sp - TC
            p['cls'] = True; at.append(p); continue
        if not p['act']:
            if hi >= p['ep'] * 1.005:
                p['act'] = True; p['tp'] = hi * (1 - 0.003)
        elif hi >= p['tp'] / (1 - 0.003):
            p['tp'] = hi * (1 - 0.003)
        ex = None
        if p['act'] and lo <= p['tp']: ex = p['tp']
        elif lo <= p['ep'] * 0.993: ex = lo
        if not ex: continue
        p['pnl'] = (ex - p['ep']) / ms * sp - TC
        p['cls'] = True; at.append(p)

pnls = np.array([t['pnl'] for t in at])
wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
wr = len(wins) / len(pnls) * 100 if len(pnls) > 0 else 0
pf = abs(sum(wins) / sum(losses)) if len(losses) > 0 and sum(losses) != 0 else 999

print(f'\n=== Stop Hunt COMBINED (TO={TO}) ===', flush=True)
print(f'Trades={len(at)} PnL={sum(pnls)/1000:.0f}K WR={wr:.1f}% PF={pf:.2f}', flush=True)
if len(wins) > 0 and len(losses) > 0:
    print(f'Avg W={np.mean(wins):.0f} Avg L={np.mean(losses):.0f}', flush=True)

td = defaultdict(lambda: {'p': [], 'n': 0})
for t in at: td[t['tk']]['p'].append(t['pnl']); td[t['tk']]['n'] += 1
for tk, d in sorted(td.items()):
    sp = np.array(d['p']); sw = sp[sp > 0]
    print(f'{tk:4s} Trades={d["n"]:>5} PnL={sum(sp)/1000:>+7.0f}K WR={len(sw)/len(sp)*100:.1f}%', flush=True)

sd = defaultdict(lambda: {'p': [], 'n': 0})
for t in at: sd[t['dir']]['p'].append(t['pnl']); sd[t['dir']]['n'] += 1
print('', flush=True)
for d, d2 in sorted(sd.items()):
    sp = np.array(d2['p']); sw = sp[sp > 0]
    wr2 = len(sw) / len(sp) * 100 if len(sp) > 0 else 0
    pf2 = abs(sum(sw) / sum(sp[sp <= 0])) if len(sp[sp <= 0]) > 0 and sum(sp[sp <= 0]) != 0 else 999
    print(f'{d:5s} Trades={d2["n"]:>5} PnL={sum(sp)/1000:>+7.0f}K WR={wr2:.1f}% PF={pf2:.2f}', flush=True)
