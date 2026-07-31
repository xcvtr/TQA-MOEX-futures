#!/usr/bin/env python3 -u
"""Dragon on MT5 FINAM M1 → M5. SL/TP from old tests."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc, pandas as pd
from strategies.dragon.prod.engine import check_signal as dragon_check

SPECS = {
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'NG': {'ms': 0.001, 'sp': 7.79568, 'go': 6406.22},
    'BR': {'ms': 0.01, 'sp': 7.79568, 'go': 13977.67},
    'SV': {'ms': 0.01, 'sp': 7.79568, 'go': 10022.27},
}
TICKERS = ['NG', 'SV', 'BR', 'MM', 'GZ']

# Dragon params from old tests
TA, TT, SL, TIMEOUT = 0.015, 0.005, 0.01, 60  # 1.5%/0.5%/1.0%/60bars

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_bars = {}
for t in TICKERS:
    rows = ch.query(f"""
        SELECT bt, opn, hi, lo, prc FROM moex.mt5_continuous
        WHERE ticker='{t}' AND bt>='2025-07-16' ORDER BY bt
    """).result_rows
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts':ts, 'opn':float(r[1]),'hi':float(r[2]),'lo':float(r[3]),'prc':float(r[4])})
    all_bars[t] = bars
    print(f'{t}: {len(bars)} M1 bars', flush=True)
ch.close()

n = min(len(all_bars[t]) for t in TICKERS)
print(f'Min bars: {n}', flush=True)

# Backtest
equity = 200000.0
peak, mtm_peak, cash_mdd, mtm_mdd = equity, equity, 0, 0
positions, trades = {}, []
m5_cache = {t: [] for t in TICKERS}

for i in range(30, n):
    # Build M5 and check signals
    if i % 5 == 4 and i > 30:
        for t in TICKERS:
            g = all_bars[t][i-5:i]
            if len(g) >= 3:
                m5 = {'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                      'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']}
                m5_cache[t].append(m5)
    
    # Check/close positions
    floating = 0.0
    for t in list(positions.keys()):
        pos = positions[t]
        bar = all_bars[t][i]
        ep, ms, sp = pos['ep'], SPECS[t]['ms'], SPECS[t]['sp']
        ex = None
        
        # SL
        slev = ep*(1-SL) if pos['dir']=='long' else ep*(1+SL)
        if (pos['dir']=='long' and bar['lo']<=slev) or (pos['dir']=='short' and bar['hi']>=slev):
            ex = slev
        # Trailing TP
        if not ex and i%5==4:
            if not pos.get('tr'):
                if (pos['dir']=='long' and bar['hi']>=ep*(1+TA)) or (pos['dir']=='short' and bar['lo']<=ep*(1-TA)):
                    pos['tr'] = True
                    pos['tl'] = bar['hi']*(1-TT) if pos['dir']=='long' else bar['lo']*(1+TT)
            if pos.get('tr'):
                if (pos['dir']=='long' and bar['lo']<=pos['tl']) or (pos['dir']=='short' and bar['hi']>=pos['tl']):
                    ex = pos['tl']
        # Timeout
        if not ex and i-pos['bi'] >= TIMEOUT:
            ex = bar['prc']
        
        if ex is not None:
            gross = (ex-ep)/ms*sp*pos.get('pct',1.0) * (1 if pos['dir']=='long' else -1)
            pnl = gross - 4
            equity += pnl
            trades.append(pnl)
            peak = max(peak, equity)
            cash_mdd = max(cash_mdd, (peak-equity)/peak*100)
            del positions[t]
        else:
            # MTM PnL
            mtm = (bar['prc']-ep)/ms*sp*pos.get('pct',1.0)*(1 if pos['dir']=='long' else -1) - 4
            floating += mtm
    
    mtm_val = equity + floating
    mtm_peak = max(mtm_peak, mtm_val)
    mtm_mdd = max(mtm_mdd, (mtm_peak-mtm_val)/mtm_peak*100) if mtm_peak > 0 else 0
    
    # Check signals on M5
    if all(len(m5_cache[t]) >= 30 for t in TICKERS if t not in positions):
        for t in TICKERS:
            if t in positions: continue
            if not m5_cache[t]: continue
            m5_bars = m5_cache[t]
            bar_data = {
                'bars_list': m5_bars,
                'prc': m5_bars[-1]['prc'],
            }
            sig = dragon_check(bar_data, t, {
                'impulse_pct': 0.3, 'retrace_max_pct': 70,
                'hump_extension': 0.1, 'lookback': 100,
            })
            if sig:
                positions[t] = {
                    'dir': sig['direction'], 'ep': sig['entry_price'],
                    'bi': i, 'pct': 1.0, 'tr': False,
                }

n_trades = len(trades)
if n_trades:
    wins = [p for p in trades if p > 0]
    losses = [p for p in trades if p <= 0]
    wr = len(wins)/n_trades*100
    pf = sum(wins)/sum(abs(p) for p in losses) if losses else 0
    ret = (equity-200000)/200000*100
else:
    wr = pf = ret = 0

print(f'\nResult: tr={n_trades} wr={wr:.1f}% pf={pf:.2f} ret={ret:+.1f}% cashMDD={cash_mdd:.2f}% mtmMDD={mtm_mdd:.2f}%')
