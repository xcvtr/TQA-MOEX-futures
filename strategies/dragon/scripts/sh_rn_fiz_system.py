#!/usr/bin/env python3 -u
"""Systematic fiz/yur filter search for SH RN."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from collections import defaultdict
from statistics import median, stdev
import clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

# Load data: mt5_continuous + futoi_iss
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

# Use Jun 2025 — Jun 2026 for training, last month for validation
rows_m1 = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='RN' AND bt>='2025-07-26' AND bt<'2026-06-01' ORDER BY bt").result_rows
rows_oi = ch.query("SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi_iss WHERE ticker='RN' AND bt>='2025-07-26' AND bt<'2026-06-01' ORDER BY bt").result_rows
ch.close()

# M1 bars
bars = []
for r in rows_m1:
    ts = r[0]; h, m = ts.hour, ts.minute
    if ts.weekday() >= 5: continue
    if h < 15 or h > 23 or (h == 23 and m > 45): continue
    bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})

# OI data: build vector with deltas
oi_raw = []
prev = None
for r in rows_oi:
    fb, fs, yb, ys = int(r[1]), int(r[2]), int(r[3]), int(r[4])
    entry = {'ts': r[0], 'fb': fb, 'fs': fs, 'yb': yb, 'ys': ys}
    if prev:
        entry['dfb'] = fb - prev[0]
        entry['dfs'] = fs - prev[1]
        entry['dyb'] = yb - prev[2]
        entry['dys'] = ys - prev[3]
        entry['fiz_net'] = fb - fs
        entry['yur_net'] = yb - ys
        entry['dfiz_net'] = (fb - fs) - (prev[0] - prev[1])
        entry['dyur_net'] = (yb - ys) - (prev[2] - prev[3])
        # Percent changes (avoid div by zero)
        total = fb + fs + yb + ys
        if total > 0:
            entry['fiz_pct'] = (fb + fs) / total * 100
            entry['yur_pct'] = (yb + ys) / total * 100
    prev = (fb, fs, yb, ys)
    oi_raw.append(entry)

oi_by_ts = {o['ts']: o for o in oi_raw if 'dfb' in o}

def find_oi(ts):
    best = None
    for k in sorted(oi_by_ts.keys()):
        if k <= ts: best = k
        else: break
    return oi_by_ts.get(best)

# SH detection + collect trades with OI
lb = 60; TA, TT, SL, TO = 0.005, 0.003, 0.007, 12; COMMISSION = 4
trades = []

for i in range(lb+5, len(bars)):
    b = bars[i]
    lo_hist = [bars[j]['lo'] for j in range(i-lb, i)]
    hi_hist = [bars[j]['hi'] for j in range(i-lb, i)]
    bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'], 'lo_hist': lo_hist, 'hi_hist': hi_hist}
    sig = sh_check(bd, 'RN', {'lookback': lb, 'retrace': 0.05})
    if not sig: continue
    
    pos = {'dir': sig['direction'], 'ep': sig['entry_price'], 'bi': i, 'shares': 1, 'tr': False}
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
        pnl = (exit_p - pos['ep']) * (-1 if pos['dir'] == 'short' else 1) - COMMISSION
        oi = find_oi(b['ts'])
        if oi:
            trades.append({
                'win': pnl > 0, 'pnl': pnl, 'dir': sig['direction'],
                **oi  # all OI fields
            })

wcnt4 = sum(1 for t in trades if t['win'])
print(f'Trades: {len(trades)} (wins={wcnt4})', flush=True)

# ── Systematic filter search ──
def test(name, pred):
    f = [t for t in trades if pred(t)]
    if len(f) < 20: return None
    w = [t for t in f if t['win']]
    l = [t for t in f if not t['win']]
    wr = len(w)/len(f)*100
    tp = sum(t['pnl'] for t in w)
    tn = abs(sum(t['pnl'] for t in l))
    pf = tp/tn if tn else 99
    return {'n': len(f), 'wr': wr, 'pf': pf, 'pnl': sum(t['pnl'] for t in f)}

baseline = test('ALL', lambda t: True)

print('\n=== SINGLE FEATURE FILTERS ===')
results = []

# Levels (not deltas)
for feat, label in [('yur_net', 'yur_net'), ('fiz_net', 'fiz_net')]:
    for cmp, cname in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0')]:
        r = test(f'{label} {cname}', lambda t, f=feat, c=cmp: c(t[f]))
        if r: results.append((f'{label} {cname}', r))

# Deltas 
for feat, label in [('dfb', 'fiz_buy_delta'), ('dfs', 'fiz_sell_delta'), 
                     ('dyb', 'yur_buy_delta'), ('dys', 'yur_sell_delta'),
                     ('dfiz_net', 'fiz_net_delta'), ('dyur_net', 'yur_net_delta')]:
    for cmp, cname in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0'),
                        (lambda x: x > 50, '>50'), (lambda x: x < -50, '<-50')]:
        r = test(f'{label} {cname}', lambda t, f=feat, c=cmp: c(t[f]))
        if r: results.append((f'{label} {cname}', r))

# Direction-based
for feat, label in [('dyur_net', 'yur_net_delta'), ('dfiz_net', 'fiz_net_delta'),
                     ('dyb', 'yur_buy'), ('dys', 'yur_sell')]:
    r = test(f'{label} >0 + LONG', lambda t, f=feat: t[f] > 0 and t['dir'] == 'long')
    if r: results.append((f'{label} >0 + LONG', r))
    r = test(f'{label} <0 + SHORT', lambda t, f=feat: t[f] < 0 and t['dir'] == 'short')
    if r: results.append((f'{label} <0 + SHORT', r))

# Agree/disagree
for feat, label in [('dyur_net', 'yur_net_delta'), ('dfiz_net', 'fiz_net_delta'),
                     ('yur_net', 'yur_net'), ('fiz_net', 'fiz_net')]:
    r = test(f'agree {label}', lambda t, f=feat: (t[f] > 0 and t['dir'] == 'long') or (t[f] < 0 and t['dir'] == 'short'))
    if r: results.append((f'agree {label}', r))
    r = test(f'vs {label}', lambda t, f=feat: (t[f] < 0 and t['dir'] == 'long') or (t[f] > 0 and t['dir'] == 'short'))
    if r: results.append((f'vs {label}', r))

# Dual combos
for f1, l1 in [('dyur_net', 'yur_d'), ('dfiz_net', 'fiz_d'), ('dyb', 'yb_d'), ('dys', 'ys_d')]:
    for f2, l2 in [('dfiz_net', 'fiz_d'), ('dyur_net', 'yur_d'), ('dfb', 'fb_d'), ('dfs', 'fs_d')]:
        if f1 == f2: continue
        for c1, cn1 in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0')]:
            for c2, cn2 in [(lambda x: x > 0, '>0'), (lambda x: x < 0, '<0')]:
                r = test(f'{l1}{cn1} + {l2}{cn2}', 
                         lambda t, a=f1, b=f2, ca=c1, cb=c2: ca(t[a]) and cb(t[b]))
                if r: results.append((f'{l1}{cn1}+{l2}{cn2}', r))

# Sort by PF (desc), min 20 trades
results.sort(key=lambda x: -x[1]['pf'])

print(f'\nBaseline: n={baseline["n"]} WR={baseline["wr"]:.1f}% PF={baseline["pf"]:.2f}')
print(f'\nTop 30 filters by PF (min 20 trades):')
hdr = f'{"Filter":40s} {"n":>5s} {"WR":>6s} {"PF":>7s}'
print(hdr)
print('-'*60)
for name, r in results[:30]:
    mark = ' ✅' if r['pf'] > baseline['pf'] * 1.2 else ''
    n_val = r['n']; wr_val = r['wr']; pf_val = r['pf']
    print(f'{name:40s} {n_val:5d} {wr_val:5.1f}% {pf_val:>6.2f}{mark}')

# Check: combo with highest coverage + good PF
print()
print('=== BEST TRADE-OFF (coverage × PF) ===')
best = []
for name, r in results:
    if r['pf'] > baseline['pf'] * 1.3 and r['n'] >= 100:
        best.append((r['n'] * r['pf'], name, r))
best.sort(key=lambda x: -x[0])
for score, name, r in best[:10]:
    n2 = r['n']; wr2 = r['wr']; pf2 = r['pf']
    print(f'  {name:40s} n={n2:4d} WR={wr2:5.1f}% PF={pf2:>5.2f} score={n2*pf2:>8.0f}')
