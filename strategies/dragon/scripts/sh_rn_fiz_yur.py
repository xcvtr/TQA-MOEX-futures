#!/usr/bin/env python3 -u
"""Check fiz/yur influence on SH RN trades."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal as sh_check

# Load M1 data + fiz/yur (M5)
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')

rows_m1 = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='RN' AND bt>='2025-07-26' AND bt<'2026-05-18' ORDER BY bt").result_rows
rows_oi = ch.query("SELECT time,fiz_buy,fiz_sell,yur_buy,yur_sell FROM moex.prices_5m_oi WHERE symbol='RN' AND time>='2025-07-26' ORDER BY time").result_rows
ch.close()

# Build M1 bars
bars = []
for r in rows_m1:
    ts = r[0]; h, m = ts.hour, ts.minute
    if ts.weekday() >= 5: continue
    if h < 15 or h > 23 or (h == 23 and m > 45): continue
    bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})

# Build OI index: timestamp → (fiz_buy, fiz_sell, yur_buy, yur_sell)
oi_map = {}
for r in rows_oi:
    oi_map[r[0]] = (int(r[1]), int(r[2]), int(r[3]), int(r[4]))

print(f'Bars: {len(bars)}, OI points: {len(oi_map)}', flush=True)

def get_oi(ts):
    """Find nearest OI bar BEFORE or AT this timestamp."""
    # Find the OI timestamp <= ts
    best = None
    for oi_ts in sorted(oi_map.keys()):
        if oi_ts <= ts:
            best = oi_ts
        else:
            break
    if best and best in oi_map:
        return oi_map[best]
    return None

# Run SH detection and collect trades with OI context
lb = 60
TA, TT, SL, TO = 0.005, 0.003, 0.007, 12
COMMISSION = 4

trades = []
for i in range(lb+5, len(bars)):
    b = bars[i]
    lo_hist = [bars[j]['lo'] for j in range(i-lb, i)]
    hi_hist = [bars[j]['hi'] for j in range(i-lb, i)]
    bd = {'prc': b['prc'], 'hi': b['hi'], 'lo': b['lo'],
          'lo_hist': lo_hist, 'hi_hist': hi_hist}
    sig = sh_check(bd, 'RN', {'lookback': lb, 'retrace': 0.05})
    if not sig: continue
    
    # Simulate trade
    pos = {'dir': sig['direction'], 'ep': sig['entry_price'], 'bi': i, 'shares': 1, 'tr': False}
    exit_price = None; pnl = None
    
    for j in range(i+1, min(i+TO+10, len(bars))):
        bb = bars[j]
        ex = None
        slv = pos['ep'] * (1 - SL) if pos['dir'] == 'long' else pos['ep'] * (1 + SL)
        if (pos['dir'] == 'long' and bb['lo'] <= slv) or (pos['dir'] == 'short' and bb['hi'] >= slv):
            ex = slv
        if not ex and not pos.get('tr'):
            act = pos['ep'] * (1 + TA) if pos['dir'] == 'long' else pos['ep'] * (1 - TA)
            if (pos['dir'] == 'long' and bb['hi'] >= act) or (pos['dir'] == 'short' and bb['lo'] <= act):
                pos['tr'] = True
                pos['tl'] = bb['hi'] * (1 - TT) if pos['dir'] == 'long' else bb['lo'] * (1 + TT)
        if not ex and pos.get('tr'):
            if (pos['dir'] == 'long' and bb['lo'] <= pos['tl']) or (pos['dir'] == 'short' and bb['hi'] >= pos['tl']):
                ex = pos['tl']
        if not ex and j - pos['bi'] >= TO:
            ex = bb['prc']
        if ex:
            exit_price = ex; break
    
    if exit_price:
        pnl = (exit_price - pos['ep']) * (-1 if pos['dir'] == 'short' else 1) - COMMISSION
        
        # Get OI context (5m bar BEFORE entry)
        oi = get_oi(b['ts'])
        if oi:
            fb, fs, yb, ys = oi
            yur_net = yb - ys  # positive = yur accumulates
            fiz_net = fb - fs  # positive = fiz accumulates
            # Normalize
            total_oi = fb + fs + yb + ys if (fb + fs + yb + ys) > 0 else 1
            yur_net_pct = yur_net / total_oi * 100
            fiz_net_pct = fiz_net / total_oi * 100
            
            trades.append({
                'win': pnl > 0,
                'pnl': pnl,
                'dir': sig['direction'],
                'yur_net': yur_net,
                'fiz_net': fiz_net,
                'yur_net_pct': yur_net_pct,
                'fiz_net_pct': fiz_net_pct,
                'yur_net_sign': 1 if yur_net > 0 else (-1 if yur_net < 0 else 0),
            })

wcnt2 = sum(1 for t in trades if t['win'])
print(f'Trades with OI: {len(trades)} (wins={wcnt2})', flush=True)

# ── Test filters ──
def test_filter(name, pred):
    filtered = [t for t in trades if pred(t)]
    if not filtered:
        msg = f'{name:35s}: NO TRADES'
        print(f'  {msg}')
        return
    n = len(filtered)
    wins = [t for t in filtered if t['win']]
    losses = [t for t in filtered if not t['win']]
    wr = len(wins)/n*100
    total_pnl = sum(t['pnl'] for t in filtered)
    pf = sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses)) if losses else float('inf')
    print(f'  {name:35s}: n={n:4d} WR={wr:5.1f}% PF={pf:>6.2f} PnL={total_pnl:>+10.0f}')

print()
print('=== BASELINE (no filter) ===')
test_filter('ALL TRADES', lambda t: True)

print()
print('=== YUR_NET filters ===')
test_filter('yur_net > 0 (юрлики покупают)', lambda t: t['yur_net'] > 0)
test_filter('yur_net < 0 (юрлики продают)', lambda t: t['yur_net'] < 0)
test_filter('yur_net > 0 AND LONG', lambda t: t['yur_net'] > 0 and t['dir'] == 'long')
test_filter('yur_net < 0 AND SHORT', lambda t: t['yur_net'] < 0 and t['dir'] == 'short')
test_filter('yur_net согласен с направлением', lambda t: (t['yur_net'] > 0 and t['dir'] == 'long') or (t['yur_net'] < 0 and t['dir'] == 'short'))

print()
print('=== FIZ_NET filters ===')
test_filter('fiz_net > 0 (физики покупают)', lambda t: t['fiz_net'] > 0)
test_filter('fiz_net < 0 (физики продают)', lambda t: t['fiz_net'] < 0)

print()
print('=== DIVERGENCE filters ===')
test_filter('yur_net > 0 AND fiz_net < 0 (smart money)', lambda t: t['yur_net'] > 0 and t['fiz_net'] < 0)
test_filter('yur_net < 0 AND fiz_net > 0 (dump)', lambda t: t['yur_net'] < 0 and t['fiz_net'] > 0)
test_filter('|yur_net| > 1000 (strong move)', lambda t: abs(t['yur_net']) > 1000)

print()
print('=== YUR_NET_PCT filters ===')
test_filter('yur_net_pct > 1%', lambda t: t['yur_net_pct'] > 1)
test_filter('yur_net_pct < -1%', lambda t: t['yur_net_pct'] < -1)
test_filter('|yur_net_pct| > 1%', lambda t: abs(t['yur_net_pct']) > 1)
test_filter('|yur_net_pct| < 1%', lambda t: abs(t['yur_net_pct']) < 1)

print()
print('=== BEST COMBOS ===')
test_filter('yur_net > 0 + LONG', lambda t: t['yur_net'] > 0 and t['dir'] == 'long')
test_filter('yur_net < 0 + SHORT', lambda t: t['yur_net'] < 0 and t['dir'] == 'short')
test_filter('yur_net > 0 + LONG OR yur_net < 0 + SHORT', lambda t: (t['yur_net'] > 0 and t['dir'] == 'long') or (t['yur_net'] < 0 and t['dir'] == 'short'))
test_filter('против yur_net', lambda t: (t['yur_net'] < 0 and t['dir'] == 'long') or (t['yur_net'] > 0 and t['dir'] == 'short'))

# Stats
print()
print('=== DISTRIBUTION ===')
yur_vals = [t['yur_net'] for t in trades]
yur_wins = [t['yur_net'] for t in trades if t['win']]
yur_loss = [t['yur_net'] for t in trades if not t['win']]

yur_med = sorted(yur_vals)[len(yur_vals)//2]
yur_w_med = sorted(yur_wins)[len(yur_wins)//2] if yur_wins else 0
yur_l_med = sorted(yur_loss)[len(yur_loss)//2] if yur_loss else 0
print(f'yur_net median (all):  {yur_med:>8.0f}')
print(f'yur_net median (wins): {yur_w_med:>8.0f}')
print(f'yur_net median (loss): {yur_l_med:>8.0f}')

# Check: what % of trades have yur_net agreeing with direction
agree = sum(1 for t in trades if (t['yur_net'] > 0 and t['dir'] == 'long') or (t['yur_net'] < 0 and t['dir'] == 'short'))
disagree = sum(1 for t in trades if (t['yur_net'] < 0 and t['dir'] == 'long') or (t['yur_net'] > 0 and t['dir'] == 'short'))
neutral = len(trades) - agree - disagree
print(f'yur_net agrees with dir:  {agree:>4d} ({agree/len(trades)*100:.1f}%)')
print(f'yur_net disagrees:        {disagree:>4d} ({disagree/len(trades)*100:.1f}%)')
print(f'yur_net neutral:          {neutral:>4d} ({neutral/len(trades)*100:.1f}%)')
