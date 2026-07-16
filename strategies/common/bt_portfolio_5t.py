#!/usr/bin/env python3
"""Portfolio backtest: Stop Hunt SHORT + LONG, Jan'25 — Jul'26"""
import clickhouse_connect as cc, numpy as np, psycopg2
from collections import defaultdict

ch = cc.get_client(host='10.0.0.64', port=8123)
P = [('GAZR','GZ'),('Si','Si'),('ROSN','RN'),('GOLD','GD'),('CNY','CR')]

pg = psycopg2.connect(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='')
cur = pg.cursor(); spe = {}
for _, t in P:
    cur.execute('SELECT step_price, min_step FROM futures.ticker_specs WHERE ticker=%s', (t,))
    r = cur.fetchone()
    if r: spe[t] = {'sp': float(r[0] or 1), 'ms': float(r[1] or 0.01)}
cur.close(); pg.close()

data = {}
for asset, tkr in P:
    df = ch.query_df(f"SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,argMax(pr_open,SYSTIME) as opn,argMax(pr_high,SYSTIME) as hi,argMax(pr_low,SYSTIME) as lo,argMax(pr_close,SYSTIME) as prc FROM moex.tradestats_fo WHERE asset_code='{asset}' AND SYSTIME>='2025-01-01' GROUP BY bt ORDER BY bt")
    if df.empty or len(df) < 1000: continue
    data[tkr] = df
    print(f'{tkr} {asset} {len(df)} bars', flush=True)

if not data:
    print("No data loaded!"); exit()

ml = max(len(df) for df in data.values())
TO = 12; TC = 4

def run_backtest(data, spe, ml, TO, TC, direction):
    """Run backtest for a given direction ('short' or 'long')"""
    from strategies.stop_hunt.prod.engine import check_signal as sh_check
    at = []; po = []
    for bi in range(50, ml):
        for tkr, df in data.items():
            if bi >= len(df): continue
            if any(not p['cls'] and p['tk'] == tkr for p in po): continue
            ms = spe[tkr]['ms']
            bd = {'prc': float(df['prc'].iloc[bi]), 'hi': float(df['hi'].iloc[bi]),
                  'lo': float(df['lo'].iloc[bi]), 'opn': float(df['opn'].iloc[bi])}
            if bi >= 20:
                bd['lo_hist'] = list(df['lo'].iloc[bi-20:bi].values)
                bd['hi_hist'] = list(df['hi'].iloc[bi-20:bi].values)
            lo_vals = bd.get('lo_hist', [])
            hi_vals = bd.get('hi_hist', [])
            if len(lo_vals) < 20: continue
            if direction == 'short':
                sig = sh_check(bd, tkr)
            else:
                hi_arr = np.array(hi_vals)
                lo_arr = np.array(lo_vals)
                rng = np.mean(hi_arr - lo_arr) if len(hi_arr) > 0 else 1
                if bd['lo'] < np.min(lo_arr) and bd['prc'] > bd['lo'] + 0.3 * rng:
                    sig = {'ticker': tkr, 'direction': 'long', 'entry_price': bd['prc'], 'strategy': 'stop_hunt'}
                else:
                    sig = None
            if not sig: continue
            ni = bi + 1
            if ni >= len(df): continue
            ep = float(df['opn'].iloc[ni]) + ms
            ep = round(ep / ms) * ms
            po.append({'tk': tkr, 'eb': ni, 'ep': ep, 'cls': False, 'pnl': 0,
                       'tp': None, 'act': False, 'ebi': bi, 'ebt': df['bt'].iloc[ni]})
        for p in po:
            if p['cls']: continue
            tkr = p['tk']; df = data[tkr]
            if bi >= len(df) or p['eb'] >= bi: continue
            hi, lo = float(df['hi'].iloc[bi]), float(df['lo'].iloc[bi])
            sp, ms = spe[tkr]['sp'], spe[tkr]['ms']
            bt = df['bt'].iloc[bi]
            # Timeout
            if (bt - p['ebt']).total_seconds() >= TO * 300:
                p['pnl'] = (float(df['prc'].iloc[bi]) - p['ep']) / ms * sp - TC
                p['cls'] = True; at.append(p); continue
            # Manage
            if direction == 'short':
                if not p['act']:
                    if hi >= p['ep'] * 1.005: p['act'] = True; p['tp'] = hi * (1 - 0.003)
                elif hi >= p['tp'] / (1 - 0.003): p['tp'] = hi * (1 - 0.003)
                ex = None
                if p['act'] and lo <= p['tp']: ex = p['tp']
                elif lo <= p['ep'] * 0.993: ex = lo
            else:
                if not p['act']:
                    if lo <= p['ep'] * 0.995: p['act'] = True; p['tp'] = lo * (1 + 0.003)
                elif lo <= p['tp'] / (1 + 0.003): p['tp'] = lo * (1 + 0.003)
                ex = None
                if p['act'] and hi >= p['tp']: ex = p['tp']
                elif hi >= p['ep'] * 1.007: ex = hi
            if not ex: continue
            p['pnl'] = (ex - p['ep']) / ms * sp - TC
            p['cls'] = True; at.append(p)
    return at

# SHORT
at1 = run_backtest(data, spe, ml, TO, TC, 'short')
print(f'\n=== Stop Hunt SHORT (TO={TO}) ===', flush=True)
if at1:
    pnls = np.array([t['pnl'] for t in at1]); wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    wr = len(wins) / len(pnls) * 100; pf = abs(sum(wins) / sum(losses)) if len(losses) > 0 and sum(losses) != 0 else 999
    print(f'Trades={len(at1)} PnL={sum(pnls)/1000:.0f}K WR={wr:.1f}% PF={pf:.2f}', flush=True)
    if len(wins) > 0 and len(losses) > 0:
        print(f'Avg W={np.mean(wins):.0f} Avg L={np.mean(losses):.0f}', flush=True)
    pt = defaultdict(list)
    for t in at1: pt[t['tk']].append(t['pnl'])
    for tk, d in sorted(pt.items()):
        sp = np.array(d); sw = sp[sp > 0]; w2 = len(sw) / len(sp) * 100
        print(f'  {tk}: Trades={len(d)} PnL={sum(d)/1000:.0f}K WR={w2:.1f}%', flush=True)
else:
    print('No trades', flush=True)

# LONG
at2 = run_backtest(data, spe, ml, TO, TC, 'long')
print(f'\n=== Stop Hunt LONG (TO={TO}) ===', flush=True)
if at2:
    pnls = np.array([t['pnl'] for t in at2]); wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    wr = len(wins) / len(pnls) * 100; pf = abs(sum(wins) / sum(losses)) if len(losses) > 0 and sum(losses) != 0 else 999
    print(f'Trades={len(at2)} PnL={sum(pnls)/1000:.0f}K WR={wr:.1f}% PF={pf:.2f}', flush=True)
    if len(wins) > 0 and len(losses) > 0:
        print(f'Avg W={np.mean(wins):.0f} Avg L={np.mean(losses):.0f}', flush=True)
    pt = defaultdict(list)
    for t in at2: pt[t['tk']].append(t['pnl'])
    for tk, d in sorted(pt.items()):
        sp = np.array(d); sw = sp[sp > 0]; w2 = len(sw) / len(sp) * 100
        print(f'  {tk}: Trades={len(d)} PnL={sum(d)/1000:.0f}K WR={w2:.1f}%', flush=True)
else:
    print('No trades', flush=True)

# Combined
all_t = at1 + at2
print(f'\n=== Combined (SHORT+LONG, TO={TO}) ===', flush=True)
if all_t:
    pnls = np.array([t['pnl'] for t in all_t]); wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    wr = len(wins) / len(pnls) * 100; pf = abs(sum(wins) / sum(losses)) if len(losses) > 0 and sum(losses) != 0 else 999
    print(f'Trades={len(all_t)} PnL={sum(pnls)/1000:.0f}K WR={wr:.1f}% PF={pf:.2f}', flush=True)
    if len(wins) > 0 and len(losses) > 0:
        print(f'Avg W={np.mean(wins):.0f} Avg L={np.mean(losses):.0f}', flush=True)
    pt = defaultdict(list)
    for t in all_t: pt[t['tk']].append(t['pnl'])
    for tk, d in sorted(pt.items()):
        sp = np.array(d); sw = sp[sp > 0]; w2 = len(sw) / len(sp) * 100
        print(f'  {tk}: Trades={len(d)} PnL={sum(d)/1000:.0f}K WR={w2:.1f}%', flush=True)
    # By direction
    dd = defaultdict(list)
    for t in all_t: dd['short' if 'tp' in t and t.get('act') is not None else 'long'].append(t['pnl'])
    # simpler: check if signal was short or long
    print(f'\nDirection breakdown:', flush=True)
    n_short = len(at1); n_long = len(at2)
    if at1:
        sp1 = np.array([t['pnl'] for t in at1])
        print(f'  short: Trades={n_short} PnL={sum(sp1)/1000:.0f}K', flush=True)
    if at2:
        sp2 = np.array([t['pnl'] for t in at2])
        print(f'  long:  Trades={n_long} PnL={sum(sp2)/1000:.0f}K', flush=True)
