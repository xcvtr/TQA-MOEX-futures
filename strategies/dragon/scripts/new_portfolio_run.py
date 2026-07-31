#!/usr/bin/env python3 -u
"""New portfolio: IR Si + Dragon GD/MM/NG + SH RN. Common pool."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from statistics import median
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state
from strategies.dragon.prod.engine import check_signal as dragon_check
from strategies.stop_hunt.prod.engine import check_signal as sh_check

COMMISSION = 4
CAPITAL = 200000

# FINAM reduced GO: NG=3664 (exchange 7328/2), others from PG
CONFIGS = [
    # IR Si 1m — baseline champ
    {'ticker': 'Si', 'strat': 'ir', 'tf': 1, 'risk': 0.02,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 6453,
     'params': {'impulse_bars': 12, 'impulse_pct': 0.3, 'cooldown': 12},
     'filters': {'trend': True}},
    # Dragon GD 10m — TRIZ TREND+VOL PF=3.68
    {'ticker': 'GD', 'strat': 'dragon', 'tf': 10, 'risk': 0.10,
     'ta': 0.015, 'tt': 0.005, 'sl': 0.01, 'to': 60,
     'ms': 0.1, 'sp': 7.84756, 'go': 15685,
     'params': {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100},
     'filters': {'trend': True, 'min_vol_ratio': 0.8}},
    # Dragon MM 5m — TRIZ TREND PF=2.05
    {'ticker': 'MM', 'strat': 'dragon', 'tf': 5, 'risk': 0.08,
     'ta': 0.015, 'tt': 0.005, 'sl': 0.01, 'to': 60,
     'ms': 0.05, 'sp': 0.5, 'go': 1404,
     'params': {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100},
     'filters': {'trend': True}},
    # SH RN 1m — PF=4.54, MDD=4.89%
    {'ticker': 'RN', 'strat': 'sh', 'tf': 1, 'risk': 0.08,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 4002,
     'params': {'lookback': 60, 'retrace': 0.05},
     'filters': {'trend': True}},
    # Dragon NG 3m — FINAM GO=3664, TREND+VOL PF=3.40
    {'ticker': 'NG', 'strat': 'dragon', 'tf': 3, 'risk': 0.10,
     'ta': 0.015, 'tt': 0.005, 'sl': 0.01, 'to': 60,
     'ms': 0.001, 'sp': 7.70611, 'go': 3664,
     'params': {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100},
     'filters': {'trend': True, 'min_vol_ratio': 0.8}},
]

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_m1 = {}
for cfg in CONFIGS:
    t = cfg['ticker']
    rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='" + t + "' AND bt>='2025-07-16' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    all_m1[t] = bars
    print(f'{t}: {len(bars)} M1 bars', flush=True)
ch.close()

def resample_n(m1, n):
    g = {}
    for b in m1:
        tm = b['ts'].hour * 60 + b['ts'].minute
        km = (tm // n) * n
        k = b['ts'].replace(minute=km % 60, hour=km // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc'], 'vol': b['vol']}
        else:
            gg = g[k]; gg['hi'] = max(gg['hi'], b['hi'])
            gg['lo'] = min(gg['lo'], b['lo']); gg['prc'] = b['prc']; gg['vol'] += b['vol']
    return sorted(g.values(), key=lambda x: x['ts'])

# Pre-build detect bars and mapping for each config
for cfg in CONFIGS:
    m1 = all_m1[cfg['ticker']]
    dbars = resample_n(m1, cfg['tf'])
    d2m = {}; di = 0
    for mi in range(len(m1)):
        if di < len(dbars) and m1[mi]['ts'] >= dbars[di]['ts']:
            d2m[di] = mi; di += 1
    cfg['_dbars'] = dbars
    cfg['_d2m'] = d2m
    ticker_name = cfg['ticker']
    print(f'{ticker_name}: {len(dbars)} detect bars ({cfg["tf"]}m)', flush=True)

# Time-aligned common pool
reset_state()
min_len = min(len(all_m1[c['ticker']]) for c in CONFIGS)
eq = float(CAPITAL)
peak = mtm_pk = float(CAPITAL)
mdd = 0
trades = []
open_pos = {}  # {ticker: pos_dict}

print(f'\nRunning common pool: {min_len} M1 bars', flush=True)

for mi in range(60, min_len):
    # ── TICK: check all open positions ──
    for cfg in CONFIGS:
        t = cfg['ticker']
        pos = open_pos.get(t)
        if not pos: continue
        b = all_m1[t][mi]
        ex = None
        
        # SL
        slv = pos['ep'] * (1 - cfg['sl']) if pos['dir'] == 'long' else pos['ep'] * (1 + cfg['sl'])
        if (pos['dir'] == 'long' and b['lo'] <= slv) or (pos['dir'] == 'short' and b['hi'] >= slv):
            ex = slv
        
        # Trail activation
        if not ex and not pos.get('tr'):
            act = pos['ep'] * (1 + cfg['ta']) if pos['dir'] == 'long' else pos['ep'] * (1 - cfg['ta'])
            if (pos['dir'] == 'long' and b['hi'] >= act) or (pos['dir'] == 'short' and b['lo'] <= act):
                pos['tr'] = True
                pos['tl'] = b['hi'] * (1 - cfg['tt']) if pos['dir'] == 'long' else b['lo'] * (1 + cfg['tt'])
        
        # Trail stop
        if not ex and pos.get('tr'):
            if (pos['dir'] == 'long' and b['lo'] <= pos['tl']) or (pos['dir'] == 'short' and b['hi'] >= pos['tl']):
                ex = pos['tl']
        
        # Timeout
        if not ex and mi - pos['bi'] >= cfg['to']:
            ex = b['prc']
        
        if ex:
            pnl = (ex - pos['ep']) / cfg['ms'] * cfg['sp'] * (-1 if pos['dir'] == 'short' else 1) * pos['shares']
            pnl -= COMMISSION * pos['shares']
            eq += pnl
            trades.append({'pnl': pnl, 'ticker': t, 'strat': cfg['strat']})
            del open_pos[t]
    
    # ── DETECT: new signals ──
    for cfg in CONFIGS:
        t = cfg['ticker']
        if t in open_pos: continue
        b = all_m1[t][mi]
        dbars = cfg['_dbars']
        d2m = cfg['_d2m']
        
        # Find current detect bar
        di = None
        for d in range(len(dbars)):
            if mi >= d2m.get(d, 999999999) and d not in cfg.get('_fired', set()):
                di = d
                break
        if di is None: continue
        
        if '_fired' not in cfg: cfg['_fired'] = set()
        cfg['_fired'].add(di)
        db = dbars[di]
        dh = dbars[:di]
        
        if len(dh) < 30: continue
        
        sig = None
        if cfg['strat'] == 'ir':
            bd = {'prc': db['prc'], 'hi': db['hi'], 'lo': db['lo'],
                  'bars_list': dh, 'vol': db.get('vol', 100),
                  'close_hist': [x['prc'] for x in dh[-20:]],
                  'lo_hist': [x['lo'] for x in dh[-20:]],
                  'hi_hist': [x['hi'] for x in dh[-20:]],
                  'vol_hist': [x.get('vol', 100) for x in dh[-20:]]}
            sig = ir_check(bd, t, cfg['params'])
        elif cfg['strat'] == 'dragon':
            sig = dragon_check({'bars_list': dh, 'prc': db['prc']}, t, cfg['params'])
        elif cfg['strat'] == 'sh':
            lb = cfg['params'].get('lookback', 60)
            bd = {'prc': db['prc'], 'hi': db['hi'], 'lo': db['lo'],
                  'lo_hist': [x['lo'] for x in dh[-lb:]],
                  'hi_hist': [x['hi'] for x in dh[-lb:]]}
            sig = sh_check(bd, t, cfg['params'])
        
        # Filters
        if sig:
            flt = cfg.get('filters', {})
            if flt.get('trend') and len(dh) >= 50:
                sma50 = sum(x['prc'] for x in dh[-50:]) / 50
                if sig['direction'] == 'long' and db['prc'] < sma50: sig = None
                elif sig['direction'] == 'short' and db['prc'] > sma50: sig = None
            if sig and flt.get('min_vol_ratio', 0) > 0:
                m1_idx = d2m.get(di, mi)
                cur_vol = all_m1[t][m1_idx]['vol'] if m1_idx < len(all_m1[t]) else 0
                vols = [all_m1[t][j]['vol'] for j in range(max(0, m1_idx-20), m1_idx) if j < len(all_m1[t])]
                if vols and median(vols) > 0 and cur_vol / median(vols) < flt['min_vol_ratio']:
                    sig = None
        
        if sig:
            shares = max(1, int(eq * cfg['risk'] / cfg['go']))
            if cfg['go'] * shares <= eq:
                open_pos[t] = {
                    'dir': sig['direction'],
                    'ep': sig['entry_price'],
                    'bi': mi,
                    'shares': shares,
                    'tr': False,
                }
    
    # ── MTM MDD ──
    floating = 0
    for cfg in CONFIGS:
        t = cfg['ticker']
        pos = open_pos.get(t)
        if not pos: continue
        b = all_m1[t][mi]
        fp = (b['prc'] - pos['ep']) / cfg['ms'] * cfg['sp'] * (-1 if pos['dir'] == 'short' else 1) * pos['shares']
        floating += fp
    mtm_val = eq + floating
    mtm_pk = max(mtm_pk, mtm_val)
    if mtm_pk > 0:
        mdd = max(mdd, (mtm_pk - mtm_val) / mtm_pk * 100)

# ── Report ──
n = len(trades)
if n:
    by_ticker = {}
    for t in trades:
        k = f"{t['ticker']}_{t['strat']}"
        if k not in by_ticker: by_ticker[k] = []
        by_ticker[k].append(t['pnl'])
    
    print(f'\n=== ПОРТФЕЛЬ (common pool, {min_len} M1 bars) ===')
    print(f'Capital: {CAPITAL:,} → {eq:,.0f} ({(eq-CAPITAL)/CAPITAL*100:+.1f}%)')
    print(f'Trades: {n} | MTM MDD: {mdd:.2f}%')
    
    total_pnl = sum(t['pnl'] for t in trades)
    wins = [t for t in trades if t['pnl'] > 0]
    wr = len(wins)/n*100
    tp = sum(t['pnl'] for t in wins)
    tn = sum(abs(t['pnl']) for t in trades if t['pnl'] <= 0)
    pf = tp/tn if tn else float('inf')
    print(f'WR: {wr:.1f}% | PF: {pf:.2f} | Total PnL: {total_pnl:+,.0f}')
    print()
    
    print('=== ПО СТРАТЕГИЯМ ===')
    for k, pnls in sorted(by_ticker.items()):
        nn = len(pnls)
        ww = [p for p in pnls if p > 0]
        wrk = len(ww)/nn*100
        pfk = sum(ww)/sum(abs(p) for p in pnls if p <= 0) if any(p <= 0 for p in pnls) else float('inf')
        print(f'{k:15s}: n={nn:4d} WR={wrk:5.1f}% ROI={sum(pnls)/CAPITAL*100:+7.1f}% PnL={sum(pnls):+9.0f} PF={pfk:.2f}')
else:
    print('НЕТ СДЕЛОК')
