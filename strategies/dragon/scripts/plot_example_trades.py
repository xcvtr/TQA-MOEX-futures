#!/usr/bin/env python3 -u
"""Generate example trades chart for IR Si 1m."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# FINAM GO
GO = 6662; MS = 1.0; SP = 1.0
TA, TT, SL, TO = 0.005, 0.003, 0.007, 12
TC = 4

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='Si' AND bt>='2026-06-01' AND bt<'2026-07-01' ORDER BY bt").result_rows
ch.close()

bars = []
for r in rows:
    ts = r[0]; h, m = ts.hour, ts.minute
    if ts.weekday() >= 5: continue
    if h < 15 or h > 23 or (h == 23 and m > 45): continue
    bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
print(f'{len(bars)} M1 bars loaded', flush=True)

# Run backtest and capture trades with context
params_ir = {'impulse_bars': 12, 'impulse_pct': 0.3, 'cooldown': 12, 'min_vol_pct': 0}
reset_state()
equity = 200000
pos = None
captured_trades = []
RISK = 0.04

for mi in range(60, len(bars)):
    b = bars[mi]
    
    # Detect every 5 bars (5-min detect)
    if not pos and mi % 5 == 4:
        dh = bars[:mi]
        if len(dh) >= 20:
            db = bars[mi]
            bd = {'prc': db['prc'], 'hi': db['hi'], 'lo': db['lo'], 'vol': 100,
                  'bars_list': dh, 'lo_hist': [x['lo'] for x in dh],
                  'hi_hist': [x['hi'] for x in dh], 'close_hist': [x['prc'] for x in dh],
                  'vol_hist': [100] * len(dh)}
            sig = ir_check(bd, 'Si', params_ir)
            if sig:
                shares = max(1, int(equity * RISK / GO))
                b_vol = bars[mi]['vol']
                if b_vol > 0:
                    shares = min(shares, max(1, int(b_vol * 0.1)))
                if GO * shares <= equity:
                    slip = MS
                    ep = sig['entry_price'] + (slip if sig['direction'] == 'long' else -slip)
                    pos = {'dir': sig['direction'], 'ep': ep, 'bi': mi, 'shares': shares, 'tr': False,
                           'entry_bar': mi, 'entry_prc': ep, 'entry_ts': b['ts']}
    
    if pos:
        ex = None
        slev = pos['ep'] * (1 - SL) if pos['dir'] == 'long' else pos['ep'] * (1 + SL)
        if (pos['dir'] == 'long' and b['lo'] <= slev) or (pos['dir'] == 'short' and b['hi'] >= slev):
            ex = slev; reason = 'SL'
        if not ex:
            if not pos.get('tr'):
                act = pos['ep'] * (1 + TA) if pos['dir'] == 'long' else pos['ep'] * (1 - TA)
                if (pos['dir'] == 'long' and b['hi'] >= act) or (pos['dir'] == 'short' and b['lo'] <= act):
                    pos['tr'] = True; pos['tl'] = b['hi'] * (1 - TT) if pos['dir'] == 'long' else b['lo'] * (1 + TT)
            if pos.get('tr') and ((pos['dir'] == 'long' and b['lo'] <= pos['tl']) or (pos['dir'] == 'short' and b['hi'] >= pos['tl'])):
                ex = pos['tl']; reason = 'TRAIL'
        if not ex and mi - pos['bi'] >= TO:
            ex = b['prc']; reason = 'TIMEOUT'
        if ex:
            pnl = (ex - pos['ep']) / MS * SP * (-1 if pos['dir'] == 'short' else 1) * pos['shares'] - TC * pos['shares']
            equity += pnl
            captured_trades.append({
                'entry_ts': pos['entry_ts'], 'exit_ts': b['ts'],
                'entry_idx': pos['entry_bar'], 'exit_idx': mi,
                'dir': pos['dir'], 'ep': pos['ep'], 'ex': ex, 'pnl': pnl,
                'shares': pos['shares'], 'reason': reason,
            })
            pos = None

print(f'Trades captured: {len(captured_trades)}', flush=True)

# Show recent trades
print(f'\nПоследние 10 сделок:')
for t in captured_trades[-10:]:
    print(f"  {t['entry_ts']} {'LONG':>5} {t['ep']:>8.0f} → {t['ex']:>8.0f} "
          f"PnL={t['pnl']:>+8.0f} {t['shares']}ct {t['reason']}")

# Plot last 5 trades
fig, axes = plt.subplots(5, 1, figsize=(14, 12), sharex=False)
fig.suptitle('IR Si 1m — Примеры сделок', fontsize=14, fontweight='bold')

for idx in range(min(5, len(captured_trades))):
    t = captured_trades[-(idx+1)]
    ax = axes[idx]
    
    start = max(0, t['entry_idx'] - 20)
    end = min(len(bars), t['exit_idx'] + 5)
    seg = bars[start:end]
    
    times = [b['ts'] for b in seg]
    closes = [b['prc'] for b in seg]
    
    in_trade = [t['entry_idx'] <= i <= t['exit_idx'] for i in range(start, end)]
    
    colors = ['#1a1a2e' if not it else ('#00ff88' if t['pnl'] > 0 else '#ff4466') for it in in_trade]
    
    for i in range(len(times)-1):
        ax.plot([times[i], times[i+1]], [closes[i], closes[i+1]], 
                color=colors[i], linewidth=1.5)
    
    # Entry/exit markers
    ax.scatter(t['entry_ts'], t['ep'], color='yellow', marker='^', s=100, zorder=5, label='Entry')
    color_exit = '#00ff88' if t['pnl'] > 0 else '#ff4466'
    exit_marker = 'v' if t['dir'] == 'long' else '^'
    ax.scatter(t['exit_ts'], t['ex'], color=color_exit, marker=exit_marker, s=100, zorder=5, label='Exit')
    
    pnl_str = f"+{t['pnl']:,.0f}" if t['pnl'] > 0 else f"{t['pnl']:,.0f}"
    ax.set_title(f"{t['dir']} {t['entry_ts'].strftime('%m/%d %H:%M')} → {t['exit_ts'].strftime('%H:%M')}  "
                 f"Entry={t['ep']:.0f} Exit={t['ex']:.0f}  PnL={pnl_str}₽  ({t['shares']}ct, {t['reason']})",
                 fontsize=9, color='white')
    ax.set_facecolor('#0d0d1a')
    ax.tick_params(colors='gray')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    for label in ax.get_xticklabels():
        label.set_rotation(30)
    ax.grid(alpha=0.15)

plt.tight_layout()
outpath = '/home/user/projects/TQA-MOEX-futures/ir_si_example_trades.png'
plt.savefig(outpath, dpi=150, facecolor='#0d0d1a')
print(f'\nSaved: {outpath}', flush=True)
