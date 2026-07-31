#!/usr/bin/env python3 -u
"""Check fiz/yur DELTAS (not levels) influence on SH RN trades."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows_m1 = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='RN' AND bt>='2025-07-26' AND bt<'2026-05-18' ORDER BY bt").result_rows
rows_oi = ch.query("SELECT time,fiz_buy,fiz_sell,yur_buy,yur_sell FROM moex.prices_5m_oi WHERE symbol='RN' AND time>='2025-07-26' ORDER BY time").result_rows
ch.close()

# M1 bars
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
    if prev:
        pfb, pfs, pyb, pys = prev
        oi_list.append({
            'ts': r[0],
            'fb': fb, 'fs': fs, 'yb': yb, 'ys': ys,
            'dfb': fb - pfb, 'dfs': fs - pfs,
            'dyb': yb - pyb, 'dys': ys - pys,
        })
    prev = (fb, fs, yb, ys)

# Index OI by ts
oi_by_ts = {o['ts']: o for o in oi_list}

def get_oi_delta(ts):
    best_ts = None
    for ots in sorted(oi_by_ts.keys()):
        if ots <= ts: best_ts = ots
        else: break
    if best_ts: return oi_by_ts[best_ts]
    return None

# SH detection
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
    exit_price = None
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
        if ex: exit_price = ex; break
    
    if exit_price:
        pnl = (exit_price - pos['ep']) * (-1 if pos['dir'] == 'short' else 1) - COMMISSION
        oi = get_oi_delta(b['ts'])
        if oi:
            trades.append({
                'win': pnl > 0, 'pnl': pnl, 'dir': sig['direction'],
                'yur_net': oi['yb'] - oi['ys'],  # level
                'dyur_net': oi['dyb'] - oi['dys'],  # delta yur net
                'dfiz_net': oi['dfb'] - oi['dfs'],  # delta fiz net
                'dyb': oi['dyb'], 'dys': oi['dys'],
                'dfb': oi['dfb'], 'dfs': oi['dfs'],
            })

wcnt3 = sum(1 for t in trades if t['win'])
print(f'Trades: {len(trades)} (wins={wcnt3})', flush=True)

def test_filter(name, pred):
    f = [t for t in trades if pred(t)]
    if not f: return
    n = len(f); w = [t for t in f if t['win']]; l = [t for t in f if not t['win']]
    wr = len(w)/n*100; tp = sum(t['pnl'] for t in w); tn = abs(sum(t['pnl'] for t in l))
    pf = tp/tn if tn else float('inf')
    print(f'  {name:35s}: n={n:4d} WR={wr:5.1f}% PF={pf:>6.2f}')

print()
print('=== DELTA YUR_NET (изменение за бар) ===')
test_filter('dyur_net > 0', lambda t: t['dyur_net'] > 0)
test_filter('dyur_net < 0', lambda t: t['dyur_net'] < 0)
test_filter('dyur_net > 50', lambda t: t['dyur_net'] > 50)
test_filter('dyur_net < -50', lambda t: t['dyur_net'] < -50)
test_filter('dyur_net > 0 + LONG', lambda t: t['dyur_net'] > 0 and t['dir'] == 'long')
test_filter('dyur_net < 0 + SHORT', lambda t: t['dyur_net'] < 0 and t['dir'] == 'short')
test_filter('согл. dyur_net + napr', lambda t: (t['dyur_net'] > 0 and t['dir'] == 'long') or (t['dyur_net'] < 0 and t['dir'] == 'short'))
test_filter('против dyur_net', lambda t: (t['dyur_net'] < 0 and t['dir'] == 'long') or (t['dyur_net'] > 0 and t['dir'] == 'short'))

print()
print('=== YUR_BUY / YUR_SELL дельты ===')
test_filter('dyb > 0 (юрлики набирают long)', lambda t: t['dyb'] > 0)
test_filter('dyb < 0 (юрлики закрывают long)', lambda t: t['dyb'] < 0)
test_filter('dys > 0 (юрлики набирают short)', lambda t: t['dys'] > 0)
test_filter('dys < 0 (юрлики закрывают short)', lambda t: t['dys'] < 0)
test_filter('dyb > 0 + LONG', lambda t: t['dyb'] > 0 and t['dir'] == 'long')
test_filter('dys > 0 + SHORT', lambda t: t['dys'] > 0 and t['dir'] == 'short')

print()
print('=== FIZ дельты ===')
test_filter('dfiz_net > 0', lambda t: t['dfiz_net'] > 0)
test_filter('dfiz_net < 0', lambda t: t['dfiz_net'] < 0)
test_filter('dfb > 0 (физики покупают)', lambda t: t['dfb'] > 0)
test_filter('dfs > 0 (физики продают)', lambda t: t['dfs'] > 0)

print()
print('=== DIVERGENCE (yur + fiz вместе) ===')
test_filter('dyur_net > 0 AND dfiz_net < 0 (smart)', lambda t: t['dyur_net'] > 0 and t['dfiz_net'] < 0)
test_filter('dyur_net < 0 AND dfiz_net > 0 (dump)', lambda t: t['dyur_net'] < 0 and t['dfiz_net'] > 0)
test_filter('dyur_net > 0 AND dfb > 0', lambda t: t['dyur_net'] > 0 and t['dfb'] > 0)
test_filter('dyb > 0 AND dfs > 0', lambda t: t['dyb'] > 0 and t['dfs'] > 0)
test_filter('dyb > 0 AND dfb > 0 (все покупают)', lambda t: t['dyb'] > 0 and t['dfb'] > 0)
test_filter('dys > 0 AND dfs > 0 (все продают)', lambda t: t['dys'] > 0 and t['dfs'] > 0)

print()
print('=== КОМБИНАЦИИ УРОВНЕЙ (как раньше, для сравнения) ===')
test_filter('yur_net > 0 (level)', lambda t: t['yur_net'] > 0)
test_filter('yur_net < 0 (level)', lambda t: t['yur_net'] < 0)

print()
print('=== ЛУЧШИЕ ПАРАМЕТРЫ ===')
test_filter('dyur_net > 0 OR dyb > 0', lambda t: t['dyur_net'] > 0 or t['dyb'] > 0)
test_filter('dyur_net > 0 AND dir=LONG', lambda t: t['dyur_net'] > 0 and t['dir'] == 'long')
