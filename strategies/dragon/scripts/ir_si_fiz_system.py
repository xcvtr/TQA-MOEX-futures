#!/usr/bin/env python3 -u
"""Systematic fiz/yur filter search for IR Si."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from collections import defaultdict
import clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# Load M1 data + futoi_iss for Si, same period as portfolio
rows_m1 = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='Si' AND bt>='2025-07-26' AND bt<'2026-07-01' ORDER BY bt").result_rows
rows_oi = ch.query("SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi_iss WHERE ticker='Si' AND bt>='2025-07-26' AND bt<'2026-07-01' ORDER BY bt").result_rows
ch.close()

# M1 bars (MOEX hours only)
bars = []
for r in rows_m1:
    ts = r[0]; h, m = ts.hour, ts.minute
    if ts.weekday() >= 5: continue
    if h < 15 or h > 23 or (h == 23 and m > 45): continue
    bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})

# OI with deltas
oi_list = []
prev = None
for r in rows_oi:
    fb, fs, yb, ys = int(r[1]), int(r[2]), int(r[3]), int(r[4])
    e = {'ts': r[0], 'fb': fb, 'fs': fs, 'yb': yb, 'ys': ys}
    if prev:
        e['dfb'] = fb - prev[0]
        e['dfs'] = fs - prev[1]
        e['dyb'] = yb - prev[2]
        e['dys'] = ys - prev[3]
        e['fiz_net'] = fb - fs
        e['yur_net'] = yb - ys
        e['dfiz_net'] = (fb - fs) - (prev[0] - prev[1])
        e['dyur_net'] = (yb - ys) - (prev[2] - prev[3])
    prev = (fb, fs, yb, ys)
    oi_list.append(e)

oi_by_ts = {o['ts']: o for o in oi_list if 'dfb' in o}

def find_oi(ts):
    best = None
    for k in sorted(oi_by_ts.keys()):
        if k <= ts: best = k
        else: break
    return oi_by_ts.get(best)

# IR parameters (same as portfolio)
GO = 6076; TA, TT, SL, TO = 0.005, 0.003, 0.007, 12
IR_PARAMS = {'impulse_bars': 12, 'impulse_pct': 0.3, 'cooldown': 12, 'min_vol_pct': 0}
COMMISSION = 4
RISK = 0.10

# Run IR detection, collect trades with OI
reset_state()
trades = []

for i in range(60, len(bars)):
    b = bars[i]
    dh = bars[:i]
    
    # IR detect
    bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'], 'vol': 100,
          'bars_list': dh[-60:],
          'lo_hist': [x['lo'] for x in dh[-50:]],
          'hi_hist': [x['hi'] for x in dh[-50:]],
          'close_hist': [x['prc'] for x in dh[-50:]],
          'vol_hist': [100] * min(len(dh), 50)}
    sig = ir_check(bd, 'Si', IR_PARAMS)
    
    # Trend filter (same as portfolio)
    if sig and len(dh) >= 50:
        sma50 = sum(x['prc'] for x in dh[-50:]) / 50
        if sig['direction'] == 'long' and b['prc'] < sma50: sig = None
        elif sig['direction'] == 'short' and b['prc'] > sma50: sig = None
    
    if not sig: continue
    
    # Simulate trade
    shares = max(1, int(200000 * RISK / GO))
    pos = {'dir': sig['direction'], 'ep': sig['entry_price'], 'bi': i, 'shares': shares, 'tr': False}
    exit_p = None
    
    for j in range(i+1, min(i+TO+10, len(bars))):
        bb = bars[j]; ex = None
        slv = pos['ep'] * (1 - SL) if pos['dir'] == 'long' else pos['ep'] * (1 + SL)
        if (pos['dir'] == 'long' and bb['lo'] <= slv) or (pos['dir'] == 'short' and bb['hi'] >= slv): ex = slv
        if not ex and not pos.get('tr'):
            act = pos['ep'] * (1 + TA) if pos['dir'] == 'long' else pos['ep'] * (1 - TA)
            if (pos['dir'] == 'long' and bb['hi'] >= act) or (pos['dir'] == 'short' and bb['lo'] <= act):
                pos['tr'] = True; pos['tl'] = bb['hi'] * (1 - TT) if pos['dir'] == 'long' else bb['lo'] * (1 + TT)
        if not ex and pos.get('tr'):
            if (pos['dir'] == 'long' and bb['lo'] <= pos['tl']) or (pos['dir'] == 'short' and bb['hi'] >= pos['tl']): ex = pos['tl']
        if not ex and j - pos['bi'] >= TO: ex = bb['prc']
        if ex: exit_p = ex; break
    
    if exit_p:
        pnl = (exit_p - pos['ep']) * (-1 if pos['dir'] == 'short' else 1) * shares - COMMISSION * shares
        oi = find_oi(b['ts'])
        if oi:
            trades.append({
                'win': pnl > 0, 'pnl': pnl, 'dir': sig['direction'],
                **oi
            })

w5 = sum(1 for t in trades if t['win'])
print(f'Si IR trades: {len(trades)} (wins={w5})', flush=True)

def test(name, pred):
    f = [t for t in trades if pred(t)]
    if len(f) < 15: return None
    w = [t for t in f if t['win']]
    l = [t for t in f if not t['win']]
    wr = len(w)/len(f)*100
    tp = sum(t['pnl'] for t in w)
    tn = abs(sum(t['pnl'] for t in l))
    pf = tp/tn if tn else 99
    return {'n': len(f), 'wr': wr, 'pf': pf}

baseline = test('ALL', lambda t: True)
print(f'Baseline: n={baseline["n"]} WR={baseline["wr"]:.1f}% PF={baseline["pf"]:.2f}')

print('\n=== ALL FILTER TESTS ===')
results = []

# 1. Levels
for feat, label in [('yur_net', 'yur_net'), ('fiz_net', 'fiz_net')]:
    for cmp, cn in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0')]:
        r = test(f'{label} {cn}', lambda t, f=feat, c=cmp: c(t[f]))
        if r: results.append((f'{label} {cn}', r))

# 2. Deltas
for feat, label in [('dfb','fiz_buy'), ('dfs','fiz_sell'), ('dyb','yur_buy'), ('dys','yur_sell'),
                     ('dfiz_net','fiz_net'), ('dyur_net','yur_net')]:
    for cmp, cn in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0'),
                     (lambda x: x > 500, '>500'), (lambda x: x < -500, '<-500')]:
        r = test(f'd{label} {cn}', lambda t, f=feat, c=cmp: c(t[f]))
        if r: results.append((f'd{label} {cn}', r))

# 3. Direction combos
for feat, label in [('dyur_net','yur_net'), ('dfiz_net','fiz_net'), ('yur_net','yur_net_l'), ('fiz_net','fiz_net_l')]:
    r = test(f'd{label}>0+LONG', lambda t, f=feat: t[f] > 0 and t['dir'] == 'long')
    if r: results.append((f'd{label}>0+LONG', r))
    r = test(f'd{label}<0+SHORT', lambda t, f=feat: t[f] < 0 and t['dir'] == 'short')
    if r: results.append((f'd{label}<0+SHORT', r))

# 4. Agree/disagree
for feat, label in [('dyur_net','yur_net_d'), ('dfiz_net','fiz_net_d'), ('yur_net','yur_net'), ('fiz_net','fiz_net')]:
    r = test(f'agree {label}', lambda t, f=feat: (t[f] > 0 and t['dir'] == 'long') or (t[f] < 0 and t['dir'] == 'short'))
    if r: results.append((f'agree {label}', r))
    r = test(f'vs {label}', lambda t, f=feat: (t[f] < 0 and t['dir'] == 'long') or (t[f] > 0 and t['dir'] == 'short'))
    if r: results.append((f'vs {label}', r))

# 5. Dual combos (top pairs)
pairs = [('dyur_net','dfiz_net'), ('dyb','dfs'), ('dys','dfb'), ('dyb','dfb'), ('dys','dfs'),
         ('dyur_net','dfb'), ('dfiz_net','dyb')]
for f1, f2 in pairs:
    for c1, cn1 in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0')]:
        for c2, cn2 in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0')]:
            r = test(f'd{f1}{cn1}+d{f2}{cn2}',
                     lambda t, a=f1, b=f2, ca=c1, cb=c2: ca(t[a]) and cb(t[b]))
            if r: results.append((f'd{f1}{cn1}+d{f2}{cn2}', r))

# Sort by PF
results.sort(key=lambda x: -x[1]['pf'])

bl_pf = baseline['pf']
print(f'\nTop 25 filters by PF:')
hdr = f'{"Filter":45s} {"n":>5s} {"WR":>6s} {"PF":>7s}'
print(hdr)
print('-'*65)
for name, r in results[:25]:
    nv = r['n']; wrv = r['wr']; pfv = r['pf']
    mark = ' ✅' if pfv > bl_pf * 1.15 else ''
    print(f'{name:45s} {nv:5d} {wrv:5.1f}% {pfv:>6.2f}{mark}')

print()
print('=== BEST TRADE-OFFS (coverage * PF) ===')
best = [(r['n']*r['pf'], name, r) for name, r in results if r['pf'] > bl_pf * 1.15 and r['n'] >= 30]
best.sort(key=lambda x: -x[0])
for score, name, r in best[:10]:
    nv = r['n']; wrv = r['wr']; pfv = r['pf']
    print(f'  {name:45s} n={nv:4d} WR={wrv:5.1f}% PF={pfv:>5.2f} score={score:>8.0f}')
