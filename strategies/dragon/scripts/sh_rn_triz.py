#!/usr/bin/env python3 -u
"""Analyze win/loss patterns for SH RN — find filters."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
from collections import defaultdict
from statistics import median, stdev
import clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

# Load data
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='RN' AND bt>='2025-07-26' ORDER BY bt").result_rows
ch.close()

bars = []
for r in rows:
    ts = r[0]; h, m = ts.hour, ts.minute
    if ts.weekday() >= 5: continue
    if h < 15 or h > 23 or (h == 23 and m > 45): continue
    bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})

print(f'Bars: {len(bars)}', flush=True)

# Simulate all trades with metadata
TA, TT, SL, TO = 0.005, 0.003, 0.007, 12
COMMISSION = 4
lb = 60

trades = []
for i in range(lb+5, len(bars)):
    b = bars[i]
    lo_hist = [bars[j]['lo'] for j in range(i-lb, i)]
    hi_hist = [bars[j]['hi'] for j in range(i-lb, i)]
    bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'],
          'lo_hist': lo_hist, 'hi_hist': hi_hist}
    sig = sh_check(bd, 'RN', {'lookback': lb, 'retrace': 0.05})
    if not sig: continue
    
    # Session: MSK = IRKT-5
    h_msk = b['ts'].hour - 5
    if h_msk < 0: h_msk += 24
    m_msk = b['ts'].minute
    
    if h_msk < 12: session = 'morning'
    elif h_msk < 15: session = 'afternoon'
    else: session = 'evening'
    
    # Range stats
    lookback_range = max(hi_hist) - min(lo_hist)
    bar_range = b['hi'] - b['lo']
    atr_est = (max(hi_hist[-20:]) - min(lo_hist[-20:])) / 20  # rough ATR
    
    # Volume
    avg_vol = sum(bars[j]['vol'] for j in range(i-20, i)) / 20 if i >= 20 else 0
    vol_ratio = b['vol'] / avg_vol if avg_vol > 0 else 1
    
    # Simulate trade
    pos = {'dir': sig['direction'], 'ep': sig['entry_price'], 'bi': i, 'shares': 1, 'tr': False}
    exit_price = None; exit_reason = None; pnl = None
    
    for j in range(i+1, min(i+TO+10, len(bars))):
        bb = bars[j]
        ex = None; reason = None
        
        slv = pos['ep'] * (1 - SL) if pos['dir'] == 'long' else pos['ep'] * (1 + SL)
        if (pos['dir'] == 'long' and bb['lo'] <= slv) or (pos['dir'] == 'short' and bb['hi'] >= slv):
            ex = slv; reason = 'SL'
        
        if not ex and not pos.get('tr'):
            act = pos['ep'] * (1 + TA) if pos['dir'] == 'long' else pos['ep'] * (1 - TA)
            if (pos['dir'] == 'long' and bb['hi'] >= act) or (pos['dir'] == 'short' and bb['lo'] <= act):
                pos['tr'] = True
                pos['tl'] = bb['hi'] * (1 - TT) if pos['dir'] == 'long' else bb['lo'] * (1 + TT)
        
        if not ex and pos.get('tr'):
            if (pos['dir'] == 'long' and bb['lo'] <= pos['tl']) or (pos['dir'] == 'short' and bb['hi'] >= pos['tl']):
                ex = pos['tl']; reason = 'TRAIL'
        
        if not ex and j - pos['bi'] >= TO:
            ex = bb['prc']; reason = 'TO'
        
        if ex:
            exit_price = ex
            exit_reason = reason
            pnl = (exit_price - pos['ep']) * (-1 if pos['dir'] == 'short' else 1) - COMMISSION
            bars_exit = j - i
            break
    
    if pnl is not None:
        trades.append({
            'win': pnl > 0,
            'pnl': pnl,
            'dir': sig['direction'],
            'session': session,
            'dow': b['ts'].weekday(),
            'hour_msk': h_msk,
            'lookback_range': lookback_range,
            'bar_range': bar_range,
            'entry_price': pos['ep'],
            'vol_ratio': vol_ratio,
            'exit_reason': exit_reason,
            'bars_held': j - i if exit_price else TO,
            'atr': atr_est,
        })

wcnt = sum(1 for t in trades if t['win'])
lcnt = sum(1 for t in trades if not t['win'])
print(f'Trades: {len(trades)} (wins={wcnt}, losses={lcnt})', flush=True)

# ── Analysis ──
wins = [t for t in trades if t['win']]
losses = [t for t in trades if not t['win']]

def analyze(feature, label):
    w_vals = [t[feature] for t in wins]
    l_vals = [t[feature] for t in losses]
    
    print(f'\n=== {label} ===')
    print(f'{"Metric":20s} {"Wins":>15s} {"Losses":>15s}')
    print('-'*52)
    
    if isinstance(w_vals[0], (int, float)):
        w_avg = sum(w_vals)/len(w_vals)
        l_avg = sum(l_vals)/len(l_vals)
        w_med = sorted(w_vals)[len(w_vals)//2]
        l_med = sorted(l_vals)[len(l_vals)//2]
        print(f'{"Mean":20s} {w_avg:>15.2f} {l_avg:>15.2f}')
        print(f'{"Median":20s} {w_med:>15.2f} {l_med:>15.2f}')
        print(f'{"Count":20s} {len(w_vals):>15d} {len(l_vals):>15d}')
    if isinstance(w_vals[0], str):
        # Categorical — show distribution
        all_cats = set(w_vals + l_vals)
        for cat in sorted(all_cats):
            w_pct = sum(1 for v in w_vals if v == cat) / len(w_vals) * 100
            l_pct = sum(1 for v in l_vals if v == cat) / len(l_vals) * 100
            print(f'{cat:20s} {w_pct:>14.1f}% {l_pct:>14.1f}%')

# Analyze each feature
analyze('session', 'SESSION (MSK)')
analyze('dow', 'DAY OF WEEK')
analyze('dir', 'DIRECTION')
analyze('exit_reason', 'EXIT REASON')
analyze('hour_msk', 'HOUR (MSK) — bucketed')
analyze('lookback_range', 'LOOKBACK RANGE (pt)')
analyze('bar_range', 'BAR RANGE (pt)')
analyze('vol_ratio', 'VOLUME RATIO (bar/avg20)')
analyze('bars_held', 'BARS HELD')

# ── Categorical DOW analysis ──
print('\n=== HOUR OF DAY (MSK) ===')
for h in range(10, 19):
    w_h = [t for t in wins if t['hour_msk'] == h]
    l_h = [t for t in losses if t['hour_msk'] == h]
    total = len(w_h) + len(l_h)
    if total > 0:
        wr = len(w_h)/total*100
        print(f'  MSK {h:02d}:00: {total:4d} trades, WR={wr:5.1f}%')

print('\n=== WIN/LOSS FEATURE SEPARATION ===')
# Best separating features
for feature, label, is_cat in [
    ('session', 'SESSION', True),
    ('dir', 'DIR', True),
    ('exit_reason', 'EXIT', True),
]:
    analyze(feature, label)
