#!/usr/bin/env python3 -u
"""Fast OI analysis for all strategies. Reads from portfolio_run output."""
import sys, os, json
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state
from strategies.dragon.prod.engine import check_signal as dragon_check
from strategies.stop_hunt.prod.engine import check_signal as sh_check

# Load OI data ONCE per ticker
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
oi_maps = {}
for t in ['Si', 'GD', 'MM', 'RN', 'NG']:
    oi_maps[t] = {}
    rows = ch.query(f"SELECT bt,buy_fiz,sell_fiz,buy_yur,sell_yur FROM moex.futoi_iss WHERE ticker='{t}' AND bt>='2025-07-26' ORDER BY bt").result_rows
    prev = None
    for r in rows:
        fb, fs, yb, ys = int(r[1]), int(r[2]), int(r[3]), int(r[4])
        e = {'fb': fb, 'fs': fs, 'yb': yb, 'ys': ys}
        if prev:
            e['dfb'] = fb - prev[0]; e['dfs'] = fs - prev[1]
            e['dyb'] = yb - prev[2]; e['dys'] = ys - prev[3]
            e['dyur_net'] = (yb - ys) - (prev[2] - prev[3])
            e['dfiz_net'] = (fb - fs) - (prev[0] - prev[1])
        prev = (fb, fs, yb, ys)
        oi_maps[t][r[0]] = e
    print(f'{t}: {len(oi_maps[t])} OI bars', flush=True)

def find_oi(ticker, ts):
    om = oi_maps.get(ticker, {})
    best = None
    for k in sorted(om.keys()):
        if k <= ts: best = k
        else: break
    return om.get(best)

# Load M1 data for all tickers
bars = {}
for t in ['Si', 'GD', 'MM', 'RN', 'NG']:
    rows = ch.query(f"SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='{t}' AND bt>='2025-07-26' ORDER BY bt").result_rows
    bl = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bl.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    bars[t] = bl
    print(f'{t}: {len(bl)} M1 bars', flush=True)

ch.close()

def resample_n(m1, n):
    g = {}
    for b in m1:
        tm = b['ts'].hour*60 + b['ts'].minute; km = (tm//n)*n
        k = b['ts'].replace(minute=km%60, hour=km//60, second=0)
        if k not in g: g[k] = {'ts':k,'opn':b['opn'],'hi':b['hi'],'lo':b['lo'],'prc':b['prc']}
        else: gg=g[k]; gg['hi']=max(gg['hi'],b['hi']); gg['lo']=min(gg['lo'],b['lo']); gg['prc']=b['prc']
    return sorted(g.values(), key=lambda x:x['ts'])

# ── Configuration ──
STRATS = [
    ('IR', 'Si', 1, 0.10, ir_check, {'impulse_bars':12,'impulse_pct':0.3,'cooldown':12,'min_vol_pct':0}),
    ('DRAGON', 'GD', 10, 0.20, dragon_check, {'impulse_pct':0.3,'retrace_max_pct':70,'hump_extension':0.1,'lookback':100}),
    ('DRAGON', 'MM', 5, 0.15, dragon_check, {'impulse_pct':0.3,'retrace_max_pct':70,'hump_extension':0.1,'lookback':100}),
    ('SH', 'RN', 1, 0.20, sh_check, {'lookback':60,'retrace':0.05}),
    ('DRAGON', 'NG', 3, 0.20, dragon_check, {'impulse_pct':0.3,'retrace_max_pct':70,'hump_extension':0.1,'lookback':100}),
]

for sname, ticker, tf, risk, fn, sparams in STRATS:
    m1 = bars[ticker]
    dbars = resample_n(m1, tf)
    COMMISSION, CAP = 4, 200000.0
    
    spec = {'Si':(1,1,6076),'GD':(0.1,7.84756,55343),'MM':(0.05,0.5,4765),'RN':(1,1,13901),'NG':(0.001,7.70611,11974)}[ticker]
    ms, sp, go = spec
    ta, tt, sl, to = (0.005,0.003,0.007,12) if sname in ('IR','SH') else (0.015,0.005,0.01,60)
    
    d2m = {}; di = 0
    for mi in range(len(m1)):
        if di < len(dbars) and m1[mi]['ts'] >= dbars[di]['ts']: d2m[di] = mi; di += 1
    
    reset_state()
    trades = []; detect_fired = set(); db_idx = 0; eq = CAP * 0.8
    
    for mi in range(60, min(len(m1), max(len(bars[t]) for t in bars))):
        b = m1[mi]
        if mi >= len(m1): continue
        
        # Tick first
        pos = None
        if trades and trades[-1].get('_pos'):
            pos = trades[-1]['_pos']
            p = pos; ex = None
            slv = p['ep']*(1-sl) if p['dir']=='long' else p['ep']*(1+sl)
            if (p['dir']=='long' and b['lo']<=slv) or (p['dir']=='short' and b['hi']>=slv): ex=slv
            if not ex and not p.get('tr'):
                act = p['ep']*(1+ta) if p['dir']=='long' else p['ep']*(1-ta)
                if (p['dir']=='long' and b['hi']>=act) or (p['dir']=='short' and b['lo']<=act):
                    p['tr']=True; p['tl']=b['hi']*(1-tt) if p['dir']=='long' else b['lo']*(1+tt)
            if not ex and p.get('tr'):
                if (p['dir']=='long' and b['lo']<=p['tl']) or (p['dir']=='short' and b['hi']>=p['tl']): ex=p['tl']
            if not ex and mi-p['bi']>=to: ex=b['prc']
            if ex:
                pnl = (ex-p['ep'])/ms*sp*(-1 if p['dir']=='short' else 1)*p['shares'] - COMMISSION*p['shares']
                eq += pnl
                trades[-1]['pnl'] = pnl
                trades[-1]['win'] = pnl > 0
                del trades[-1]['_pos']
                pos = None
        
        # Detect
        if not pos and db_idx < len(dbars) and db_idx not in detect_fired and mi >= d2m.get(db_idx, 999999999):
            detect_fired.add(db_idx)
            db = dbars[db_idx]; dh = dbars[:db_idx]
            
            sig = None
            if len(dh) >= 30:
                if sname == 'IR':
                    bd = {'prc':db['prc'],'hi':db['hi'],'lo':db['lo'],'vol':100,
                          'bars_list':dh[-60:],'close_hist':[x['prc'] for x in dh[-50:]],
                          'lo_hist':[x['lo'] for x in dh[-50:]],'hi_hist':[x['hi'] for x in dh[-50:]],
                          'vol_hist':[100]*min(len(dh),50)}
                    sig = fn(bd, ticker, sparams)
                elif sname == 'SH':
                    bd = {'prc':db['prc'],'hi':db['hi'],'lo':db['lo'],
                          'lo_hist':[x['lo'] for x in dh[-60:]],'hi_hist':[x['hi'] for x in dh[-60:]]}
                    sig = fn(bd, ticker, sparams)
                else:
                    sig = fn({'bars_list':dh, 'prc':db['prc']}, ticker, sparams)
                
                if sig and len(dh) >= 50:
                    sma50 = sum(x['prc'] for x in dh[-50:])/50
                    if sig['direction']=='long' and db['prc']<sma50: sig=None
                    elif sig['direction']=='short' and db['prc']>sma50: sig=None
            
            if sig:
                shares = max(1, int(eq*risk/go))
                if go*shares <= eq:
                    slip = ms
                    ep = sig['entry_price'] + (slip if sig['direction']=='long' else -slip)
                    trades.append({
                        'dir': sig['direction'], 'ep': ep, 'ts': b['ts'],
                        'pnl': 0, 'win': False, '_pos': {
                            'dir':sig['direction'],'ep':ep,'bi':mi,'shares':shares,'tr':False
                        }
                    })
            db_idx += 1
    
    # Clean up open positions
    trades = [t for t in trades if t['pnl'] != 0]
    
    # Match OI
    for t in trades:
        oi = find_oi(ticker, t['ts'])
        if oi: t.update(oi)
    
    total_pnl = sum(t['pnl'] for t in trades)
    w = [t for t in trades if t['win']]; l = [t for t in trades if not t['win']]
    wr = len(w)/len(trades)*100 if trades else 0
    pf = sum(t['pnl'] for t in w)/abs(sum(t['pnl'] for t in l)) if l and sum(t['pnl'] for t in l) else 0
    
    print(f'\n=== {sname} {ticker} ===')
    print(f'ALL: n={len(trades):4d} W={len(w):4d} L={len(l):4d} WR={wr:.1f}% PnL={total_pnl:>+10,.0f} PF={pf:.2f}')
    
    # Test filters
    tests = [
        ('dfb<0', lambda t: 'dfb' in t and t['dfb'] < 0),
        ('dfb>0', lambda t: 'dfb' in t and t['dfb'] > 0),
        ('dyb>0', lambda t: 'dyb' in t and t['dyb'] > 0),
        ('dyb<0', lambda t: 'dyb' in t and t['dyb'] < 0),
        ('dys>0', lambda t: 'dys' in t and t['dys'] > 0),
        ('dfs>0', lambda t: 'dfs' in t and t['dfs'] > 0),
        ('dyur>0', lambda t: 'dyur_net' in t and t['dyur_net'] > 0),
        ('dyur<0', lambda t: 'dyur_net' in t and t['dyur_net'] < 0),
        ('dfiz>0', lambda t: 'dfiz_net' in t and t['dfiz_net'] > 0),
        ('dfiz<0', lambda t: 'dfiz_net' in t and t['dfiz_net'] < 0),
        ('dfb>0+LONG', lambda t: 'dfb' in t and t['dfb'] > 0 and t['dir'] == 'long'),
        ('dyb<0+SHORT', lambda t: 'dyb' in t and t['dyb'] < 0 and t['dir'] == 'short'),
        ('dfb>0+dfiz<0', lambda t: 'dfb' in t and t['dfb'] > 0 and t.get('dfiz_net',0) < 0),
        ('dyb<0+dfs>0', lambda t: 'dyb' in t and t['dyb'] < 0 and t.get('dfs',0) > 0),
    ]
    
    for fname, pred in tests:
        ft = [t for t in trades if pred(t)]
        if len(ft) >= 15:
            fw = [t for t in ft if t['win']]; fl = [t for t in ft if not t['win']]
            fpf = sum(t['pnl'] for t in fw)/abs(sum(t['pnl'] for t in fl)) if fl and sum(t['pnl'] for t in fl) else 0
            fp = sum(t['pnl'] for t in ft)
            mark = ' ✅' if fpf > pf * 1.3 else ''
            print(f'  {fname:15s}: n={len(ft):4d} PF={fpf:>6.2f} PnL={fp:>+10,.0f}{mark}')
