#!/usr/bin/env python3 -u
"""Dragon TF sweep — M1 resample to detect TF, FINAM GO, trend filter."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.dragon.prod.engine import check_signal as dragon_check

SPECS = {
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 1475, 'lot': 1},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 949, 'lot': 100},
}
TA, TT, SL, TO = 0.015, 0.005, 0.01, 60
TC = 4

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_m1 = {}
for t in ['MM', 'GZ']:
    rows = ch.query("SELECT bt,opn,hi,lo,prc FROM moex.mt5_continuous WHERE ticker='" + t + "' AND bt>='2025-07-16' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    print(f'{t}: {len(bars)} M1 bars', flush=True)
    all_m1[t] = bars
ch.close()

def resample_to_N(m1_bars, N):
    g = {}
    for b in m1_bars:
        total_m = b['ts'].hour * 60 + b['ts'].minute
        k_min = (total_m // N) * N
        k = b['ts'].replace(minute=k_min % 60, hour=k_min // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']}
        else:
            gg = g[k]
            gg['hi'] = max(gg['hi'], b['hi'])
            gg['lo'] = min(gg['lo'], b['lo'])
            gg['prc'] = b['prc']
    return sorted(g.values(), key=lambda x: x['ts'])

def run_ticker(ticker, tf):
    m1 = all_m1[ticker]
    dbars = resample_to_N(m1, tf)
    ms, sp, go = SPECS[ticker]['ms'], SPECS[ticker]['sp'], SPECS[ticker]['go']
    
    # Map: detect bar index -> M1 bar index
    d2m = {}
    di = 0
    for mi in range(len(m1)):
        if di < len(dbars) and m1[mi]['ts'] >= dbars[di]['ts']:
            d2m[di] = mi
            di += 1
    
    out = []
    for risk_pct in [1, 2, 3, 4, 5, 7, 10, 15]:
        risk = risk_pct / 100.0
        equity = 200000.0; peak = equity; mtm_peak = equity
        cash_mdd = mtm_mdd = 0; pos = None; trades = []
        detect_fired = set()
        db_idx = 0
        
        for mi in range(60, len(m1)):
            b = m1[mi]
            
            # Detect on new detect bar
            if not pos and db_idx < len(dbars) and db_idx not in detect_fired and mi >= d2m.get(db_idx, 999999999):
                detect_fired.add(db_idx)
                db = dbars[db_idx]
                db_hist = dbars[:db_idx]
                if len(db_hist) >= 30:
                    sig = dragon_check({'bars_list': db_hist, 'prc': db['prc']}, ticker,
                                       {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100})
                    if sig:
                        if len(db_hist) >= 50:
                            sma50 = sum(x['prc'] for x in db_hist[-50:]) / 50
                            if sig['direction'] == 'long' and db['prc'] < sma50:
                                sig = None
                            elif sig['direction'] == 'short' and db['prc'] > sma50:
                                sig = None
                    if sig:
                        shares = max(1, int(equity * risk / go))
                        pos = {'dir': sig['direction'], 'ep': sig['entry_price'], 'bi': mi, 'shares': shares, 'tr': False}
                db_idx += 1
            
            # Tick on M1
            if pos:
                ex = None
                slev = pos['ep'] * (1 - SL) if pos['dir'] == 'long' else pos['ep'] * (1 + SL)
                if (pos['dir'] == 'long' and b['lo'] <= slev) or (pos['dir'] == 'short' and b['hi'] >= slev):
                    ex = slev
                if not ex:
                    if not pos.get('tr'):
                        act = pos['ep'] * (1 + TA) if pos['dir'] == 'long' else pos['ep'] * (1 - TA)
                        if (pos['dir'] == 'long' and b['hi'] >= act) or (pos['dir'] == 'short' and b['lo'] <= act):
                            pos['tr'] = True
                            pos['tl'] = b['hi'] * (1 - TT) if pos['dir'] == 'long' else b['lo'] * (1 + TT)
                    if pos.get('tr'):
                        if (pos['dir'] == 'long' and b['lo'] <= pos['tl']) or (pos['dir'] == 'short' and b['hi'] >= pos['tl']):
                            ex = pos['tl']
                if not ex and mi - pos['bi'] >= TO:
                    ex = b['prc']
                if ex:
                    pnl = (ex - pos['ep']) / ms * sp
                    if pos['dir'] == 'short':
                        pnl = -pnl
                    pnl = pnl * pos['shares'] - TC * pos['shares']
                    equity += pnl; trades.append(pnl)
                    peak = max(peak, equity)
                    cash_mdd = max(cash_mdd, (peak - equity) / peak * 100)
                    pos = None
            
            floating = 0
            if pos:
                fp = (b['prc'] - pos['ep']) / ms * sp
                if pos['dir'] == 'short':
                    fp = -fp
                floating = fp * pos['shares']
            mtm_val = equity + floating
            mtm_peak = max(mtm_peak, mtm_val)
            if mtm_peak > 0:
                mtm_mdd = max(mtm_mdd, (mtm_peak - mtm_val) / mtm_peak * 100)
        
        n = len(trades)
        if n:
            wins = [p for p in trades if p > 0]
            losses = [p for p in trades if p <= 0]
            wr = len(wins)/n*100
            pf = sum(wins)/sum(abs(p) for p in losses) if losses else 0
            ret = (equity-200000)/200000*100
            out.append((risk_pct, ret, mtm_mdd, pf, wr, n))
    return out

for ticker in ['MM', 'GZ']:
    for tf in [3, 5, 10, 15]:
        res = run_ticker(ticker, tf)
        print(f'\n=== {ticker} {tf}мин ===')
        print('risk%  ret%     MDD    PF     WR    Trades')
        for r in res:
            risk_pct, ret, mdd, pf, wr, n = r
            mark = ' ✅' if mdd <= 20 else ''
            print(f'{risk_pct:3d}%  {ret:>+7.1f}%  {mdd:>6.2f}%  {pf:>5.2f}  {wr:5.1f}%  {n:4d}{mark}')
