#!/usr/bin/env python3 -u
"""Multi-year simulation with MTM MDD, compounds yearly."""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal
import clickhouse_connect as cc

TC, KNUR, START_EQ, RISK = 4, 0.5, 200000, 10

ALL_TICKERS = {
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 10259.52},
    'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 15353.35},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 17164.0},
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
    'AF': {'ms': 10.0, 'sp': 1.0, 'go': 1010.0},
    'HY': {'ms': 10.0, 'sp': 1.0, 'go': 466.0},
    'VB': {'ms': 10.0, 'sp': 1.0, 'go': 1859.5},
    'TT': {'ms': 1.0, 'sp': 1.0, 'go': 6568.0},
    'MX': {'ms': 10.0, 'sp': 25.0, 'go': 3712.5},
}
PRIORITY = ['NG','SV','BR','MM','RN','CR','GZ','Si','AF','HY','VB','TT','MX']


def load_bars(t):
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    rows = ch.query(f"SELECT bt, opn, hi, lo, prc FROM moex.mt5_continuous WHERE ticker='{t}' AND bt >= '2025-07-16' ORDER BY bt").result_rows
    ch.close()
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars


def select(equity):
    gl = equity * KNUR
    sel = []
    for t in PRIORITY:
        n = max(len(sel), 1)
        if ALL_TICKERS[t]['go'] * 2 <= gl / n:
            sel.append(t)
    if len(sel) < 2: sel = ['NG','SV']
    return sel, {t: 1/len(sel) for t in sel}


def run_year(equity):
    sel, alloc = select(equity)
    bars_cache = {}
    for t in sel:
        b = load_bars(t)
        if b: bars_cache[t] = b
    
    tickers = sorted(bars_cache.keys(), key=lambda x: PRIORITY.index(x) if x in PRIORITY else 99)
    if not tickers: return equity, 0, 0, tickers
    
    min_len = min(len(bars_cache[t]) for t in tickers)
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    pos, m5_cache, go_used = {}, {t: [] for t in tickers}, 0
    go_limit, teq = equity * KNUR, {t: equity * alloc[t] for t in tickers}
    eq, peak_mtm = float(equity), float(equity)
    mtm_mdd, trades = 0, 0

    for i in range(30, min_len):
        if i % 5 == 0:
            for t in tickers:
                g = bars_cache[t][i-5:i]
                if len(g) >= 3:
                    m5_cache[t].append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                                        'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
        for t in tickers:
            p = pos.get(t)
            if p is None: continue
            bar = bars_cache[t][i]; ep = p['ep']
            ms, sp = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp']; ex = None
            slev = ep*(1-sl) if p['dir']=='long' else ep*(1+sl)
            if (p['dir']=='long' and bar['lo']<=slev) or (p['dir']=='short' and bar['hi']>=slev): ex=slev
            if not ex and i%5==4:
                if not p.get('tr'):
                    if (p['dir']=='long' and bar['hi']>=ep*(1+ta)) or (p['dir']=='short' and bar['lo']<=ep*(1-ta)):
                        p['tr']=True; p['tl']=bar['hi']*(1-tt) if p['dir']=='long' else bar['lo']*(1+tt)
                if p.get('tr'):
                    if (p['dir']=='long' and bar['lo']<=p['tl']) or (p['dir']=='short' and bar['hi']>=p['tl']): ex=p['tl']
            if not ex and i-p['bi']>=60: ex=bar['prc']
            if ex is not None:
                raw = ((ex-ep)/ms*sp - TC) * p['contracts']
                pnl = raw if p['dir']=='long' else -raw
                teq[t] += pnl; eq += pnl
                go_used -= ALL_TICKERS[t]['go'] * p['contracts']
                trades += 1; del pos[t]
        
        mtm_eq = eq
        for t in tickers:
            p = pos.get(t)
            if p is None: continue
            bar = bars_cache[t][i]; ms, sp = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp']
            raw = ((bar['prc'] - p['ep']) / ms * sp) * p['contracts']
            mtm_eq += (raw if p['dir']=='long' else -raw)
        peak_mtm = max(peak_mtm, mtm_eq)
        mtm_mdd = max(mtm_mdd, (peak_mtm - mtm_eq) / peak_mtm * 100)
        
        if i % 5 == 0:
            for t in tickers:
                if t in pos or len(m5_cache.get(t, [])) < 6: continue
                ms, sp, go = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp'], ALL_TICKERS[t]['go']
                sig = check_signal({'prc': m5_cache[t][-110:][-1]['prc'], 'bars_list': m5_cache[t][-110:]}, t, dp)
                if not sig: continue
                ra = teq.get(t, eq/len(tickers)) * RISK / 100
                sc = sig['entry_price'] * sl / ms * sp + TC
                c = max(1, int(ra / sc)) if sc > 0 else 1
                if go_used + go * c > go_limit: continue
                pos[t] = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                          'tr': False, 'tl': None, 'contracts': c}
                go_used += go * c
    
    return eq, (eq-equity)/equity*100, mtm_mdd, tickers


if __name__ == '__main__':
    print(f'{"="*60}')
    print(f'  SIMULATION: RISK={RISK}%, KNUR={KNUR}, GO/2, TC={TC}')
    print(f'  Starting capital: {START_EQ:,}')
    print(f'{"="*60}')
    print(f'{"Year":>4} {"Capital":>10} {"Return":>8} {"MTM MDD":>8}  Tickers')
    print(f'{"-"*60}')
    
    eq = START_EQ
    peak_mtm_all = START_EQ
    max_mtm_mdd = 0
    year_mtm_mdd = 0
    
    for y in range(1, 6):
        new_eq, ret, mdd, tickers = run_year(eq)
        peak_mtm_all = max(peak_mtm_all, new_eq)
        max_mtm_mdd = max(max_mtm_mdd, mdd)
        year_mtm_mdd = max(year_mtm_mdd, mdd)
        
        tk_str = ','.join(tickers[:6])
        if len(tickers) > 6: tk_str += f'+{len(tickers)-6}'
        print(f'  {y:3d}  {new_eq:>8.0f}  {ret:>+6.1f}%  {mdd:>6.2f}%  [{tk_str}]')
        
        eq = new_eq
    
    total_ret = (eq-START_EQ)/START_EQ*100
    cagr = ((eq/START_EQ)**(1/5)-1)*100
    print(f'{"-"*60}')
    print(f'  5Y: {START_EQ:,} -> {eq:,.0f} ({total_ret:+.1f}%)')
    print(f'  CAGR: {cagr:.1f}%/год')
    print(f'  Max MTM MDD: {max_mtm_mdd:.2f}%')
