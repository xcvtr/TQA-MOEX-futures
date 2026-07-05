#!/usr/bin/env python3
"""Scan all tickers for Stop Hunt performance."""
import clickhouse_connect as cc, numpy as np, psycopg2
from strategies.stop_hunt.prod.engine import check_signal as sh_check
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ch = cc.get_client(host='10.0.0.64', port=8123, database='moex')

# Get all assets with sufficient data
rows = ch.query("SELECT DISTINCT asset_code FROM moex.tradestats_fo WHERE SYSTIME >= '2025-01-01'").result_rows
assets = []
for r in rows:
    a = r[0]
    if not a:
        continue
    cnt = ch.query(f"SELECT count(DISTINCT toStartOfDay(SYSTIME)) FROM moex.tradestats_fo WHERE asset_code = '{a}' AND SYSTIME >= '2025-01-01'").result_rows[0][0]
    if cnt and cnt > 50:
        assets.append((a, cnt))

print(f'Found {len(assets)} assets with data', flush=True)

# Get specs from PG
pg = psycopg2.connect(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='')
cur = pg.cursor()
spe = {}
for a, _ in assets:
    cur.execute('SELECT ticker, step_price, min_step, go, lot_volume, pct FROM futures.ticker_specs WHERE asset_code = %s', (a,))
    r = cur.fetchone()
    if r:
        pct = float(r[5]) if len(r) > 5 else 1.0
        spe[a] = {'tk': r[0], 'sp': float(r[1] or 1), 'ms': float(r[2] or 0.01), 'go': float(r[3] or 0), 'lot': int(r[4] or 1), 'pct': pct}
cur.close()
pg.close()
print(f'Found specs for {len(spe)} tickers', flush=True)

# Backtest each ticker
results = []
TC = 4
TO = 12
for i, (asset, tinfo) in enumerate(sorted(spe.items())):
    s = tinfo
    tkr = s['tk']
    sp = s['sp']
    ms = s['ms']
    go = s['go']
    lot = s['lot']
    pt_cost = sp / ms if ms > 0 else 1  # RUB per point

    try:
        df = ch.query_df(f"SELECT toStartOfInterval(SYSTIME, INTERVAL 5 MINUTE) as bt, argMax(pr_open, SYSTIME) as opn, argMax(pr_high, SYSTIME) as hi, argMax(pr_low, SYSTIME) as lo, argMax(pr_close, SYSTIME) as prc FROM moex.tradestats_fo WHERE asset_code = '{asset}' AND SYSTIME >= '2025-01-01' GROUP BY bt ORDER BY bt")
        if df.empty or len(df) < 200:
            continue
    except Exception as e:
        print(f'  {tkr:6s} {asset:12s} DATA ERR: {str(e)[:60]}', flush=True)
        continue

    n = len(df)
    at = []
    po = []
    for bi in range(50, n):
        for p in po:
            if p['cls']:
                continue
            if bi >= n or p['eb'] >= bi:
                continue
            hi2 = float(df['hi'].iloc[bi])
            lo2 = float(df['lo'].iloc[bi])
            if bi - p['ebi'] >= TO:
                p['pnl'] = (float(df['prc'].iloc[bi]) - p['ep']) / ms * sp * lot * pct - TC
                p['cls'] = True
                at.append(p)
                continue
            if not p['act']:
                if hi2 >= p['ep'] * 1.005:
                    p['act'] = True
                    p['tp'] = hi2 * (1 - 0.003)
            elif hi2 >= p['tp'] / (1 - 0.003):
                p['tp'] = hi2 * (1 - 0.003)
            ex = None
            if p['act'] and lo2 <= p['tp']:
                ex = p['tp']
            elif lo2 <= p['ep'] * 0.993:
                ex = lo2
            if ex:
                p['pnl'] = (ex - p['ep']) / ms * sp * lot * pct - TC
                p['cls'] = True
                at.append(p)

        if any(not p['cls'] and p['tk'] == tkr for p in po):
            continue
        bd = {
            'prc': float(df['prc'].iloc[bi]),
            'hi': float(df['hi'].iloc[bi]),
            'lo': float(df['lo'].iloc[bi])
        }
        if bi >= 20:
            bd['lo_hist'] = list(df['lo'].iloc[bi - 20:bi].values)
            bd['hi_hist'] = list(df['hi'].iloc[bi - 20:bi].values)
        sig = sh_check(bd, tkr)
        if not sig:
            continue
        ni = bi + 1
        if ni >= n:
            continue
        ep = float(df['opn'].iloc[ni]) + ms
        ep = round(ep / ms) * ms
        ep = max(ep, 0.01)
        po.append({'tk': tkr, 'eb': ni, 'ep': ep, 'cls': False, 'pnl': 0, 'tp': None, 'act': False, 'ebi': bi})

    pnls = np.array([t['pnl'] for t in at])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    n_trades = len(pnls)
    wr = len(wins) / n_trades * 100 if n_trades > 0 else 0
    pf = abs(sum(wins) / sum(losses)) if len(losses) > 0 and sum(losses) != 0 else 0
    tp = sum(pnls)
    sharpe = (np.mean(pnls) / np.std(pnls) * np.sqrt(252 * 78)) if np.std(pnls) > 0 and n_trades > 0 else 0

    avg_trade = np.mean(pnls) if n_trades > 0 else 0
    expected_return_annual = avg_trade * n_trades / 1.5  # per year
    roi_pct = expected_return_annual / max(go, 1) * 100 if go > 0 else 0

    results.append((tp, tkr, asset, wr, pf, n_trades, sharpe, roi_pct, avg_trade, go, lot, pt_cost))

    if (i + 1) % 10 == 0:
        print(f'  {i+1}/{len(spe)}', flush=True)

# Results
print(f'\n{"Tkr":5s} {"Asset":12s} {"PnL":>10s} {"WR":>5s} {"PF":>5s} {"Tr":>5s} {"Sharpe":>7s} {"ROI":>8s} {"Avg":>8s} {"GO":>6s} {"Lot":>4s} {"1pt":>8s}')
print('-' * 95)
for tp, tkr, asset, wr, pf, nt, sh, roi, avg, go, lot, pt in sorted(results, key=lambda x: x[0], reverse=True):
    print(f'{tkr:5s} {asset:12s} {tp/1000:>+9.0f}K {wr:>4.1f}% {pf:>4.2f} {nt:>5} {sh:>7.2f} {roi:>7.0f}% {avg:>+7.0f} {go:>6.0f} {lot:>4} {pt:>8.0f}')

# Add to portfolio suggestion
print(f'\n=== Best candidates for portfolio ===')
top = [r for r in sorted(results, key=lambda x: x[6], reverse=True) if r[5] >= 200 and r[3] >= 50 and r[0] > 0][:10]
print(f'Top by Sharpe ratio (min 200 trades, WR>50%):')
for r in top:
    print(f'  {r[1]:5s} {r[2]:12s} PnL={r[0]/1000:>+7.0f}K WR={r[3]:.1f}% PF={r[4]:.2f} Trades={r[5]:>5} Sharpe={r[6]:.2f} ROI={r[7]:.0f}%')
