#!/usr/bin/env python3 -u
"""IR sweep — M1 resample to detect TF, FINAM GO, all tickers."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state

SPECS = {
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 6662},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 17164},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 4054},
    'GD': {'ms': 0.1, 'sp': 7.84756, 'go': 16607},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 949},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1822},
}
TA, TT, SL, TO = 0.005, 0.003, 0.007, 12  # IR trailing params
TC = 4

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_m1 = {}
for t in ['Si', 'BR', 'RN']:
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
    
    d2m = {}
    di = 0
    for mi in range(len(m1)):
        if di < len(dbars) and m1[mi]['ts'] >= dbars[di]['ts']:
            d2m[di] = mi
            di += 1
    
    params_ir = {'impulse_bars': 12, 'impulse_pct': 0.3, 'cooldown': 12, 'min_vol_pct': 0}
    
    out = []
    for risk_pct in [1, 2, 3, 4, 5, 7, 10, 15]:
        risk = risk_pct / 100.0
        reset_state()
        equity = 200000.0; peak = equity; mtm_peak = equity
        cash_mdd = mtm_mdd = 0; pos = None; trades = []
        detect_fired = set()
        db_idx = 0
        
        for mi in range(60, len(m1)):
            b = m1[mi]
            
            if not pos and db_idx < len(dbars) and db_idx not in detect_fired and mi >= d2m.get(db_idx, 999999999):
                detect_fired.add(db_idx)
                db = dbars[db_idx]
                db_hist = dbars[:db_idx]
                
                if len(db_hist) >= 20:
                    bd = {
                        'prc': db['prc'], 'hi': db['hi'], 'lo': db['lo'],
                        'vol': 100,
                        'bars_list': db_hist,
                        'lo_hist': [x['lo'] for x in db_hist],
                        'hi_hist': [x['hi'] for x in db_hist],
                        'close_hist': [x['prc'] for x in db_hist],
                        'vol_hist': [100] * len(db_hist),
                    }
                    sig = ir_check(bd, ticker, params_ir)
                    if sig:
                        shares = max(1, int(equity * risk / go))
                        pos = {'dir': sig['direction'], 'ep': sig['entry_price'], 'bi': mi, 'shares': shares, 'tr': False}
                db_idx += 1
            
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

for ticker in ['Si']:
    for tf in [1, 3, 30]:
        res = run_ticker(ticker, tf)
        has_ok = any(r[2] <= 20 for r in res)
        if not has_ok:
            continue
        print(f'\n=== {ticker} detect={tf}мин ===')
        print('risk%  ret%      MDD    PF     WR    Trades')
        for r in res:
            risk_pct, ret, mdd, pf, wr, n = r
            mark = ' ✅' if mdd <= 20 else ''
            print(f'{risk_pct:3d}%  {ret:>+8.1f}%  {mdd:>6.2f}%  {pf:>5.2f}  {wr:5.1f}%  {n:4d}{mark}')
