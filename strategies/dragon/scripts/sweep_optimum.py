#!/usr/bin/env python3 -u
"""Sweep: combo × risk → find max PnL with MTM MDD <= 20%."""
import sys, itertools
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal
import clickhouse_connect as cc

TC, KNUR, CAP = 4, 0.5, 200000

ALL_TICKERS = {
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 10259.52},
    'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 15353.35},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 17164.0},
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
}
PRIORITY = ['MM','CR','GZ','RN','NG','SV','BR']


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


def run(tickers, risk):
    bars_cache = {}
    for t in tickers:
        b = load_bars(t)
        if b: bars_cache[t] = b
    tickers = sorted(bars_cache.keys(), key=lambda x: PRIORITY.index(x) if x in PRIORITY else 99)
    if len(tickers) < 2: return None
    
    min_len = min(len(bars_cache[t]) for t in tickers)
    alloc = {t: 1.0/len(tickers) for t in tickers}
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    pos, m5, go_used = {}, {t: [] for t in tickers}, 0
    go_limit, teq = CAP * KNUR, {t: CAP * alloc[t] for t in tickers}
    eq, peak_mtm, mtm_mdd = float(CAP), float(CAP), 0
    trades = 0
    
    for i in range(30, min_len):
        if i % 5 == 0:
            for t in tickers:
                g = bars_cache[t][i-5:i]
                if len(g) >= 3:
                    m5[t].append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
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
            raw = ((bar['prc']-p['ep'])/ms*sp) * p['contracts']
            mtm_eq += (raw if p['dir']=='long' else -raw)
        peak_mtm = max(peak_mtm, mtm_eq)
        mtm_mdd = max(mtm_mdd, (peak_mtm-mtm_eq)/peak_mtm*100)
        
        if i % 5 == 0:
            for t in tickers:
                if t in pos or len(m5.get(t, [])) < 6: continue
                ms, sp, go = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp'], ALL_TICKERS[t]['go']
                sig = check_signal({'prc': m5[t][-110:][-1]['prc'], 'bars_list': m5[t][-110:]}, t, dp)
                if not sig: continue
                ra = teq.get(t, eq/len(tickers)) * risk / 100
                sc = sig['entry_price'] * sl / ms * sp + TC
                c = max(1, int(ra/sc)) if sc > 0 else 1
                if go_used + go*c > go_limit: continue
                pos[t] = {'bi': i, 'ep': sig['entry_price'], 'dir': sig['direction'],
                          'tr': False, 'tl': None, 'contracts': c}
                go_used += go*c
    
    ret = (eq-CAP)/CAP*100
    return ret, mtm_mdd, trades, tickers


if __name__ == '__main__':
    results = []
    # Тест от 2 до 5 тикеров, все комбинации из PRIORITY
    for n in range(2, 6):
        for combo in itertools.combinations(PRIORITY, n):
            for risk in [5, 7, 10, 12, 15, 17, 20]:
                r = run(list(combo), risk)
                if r and r[1] <= 20:
                    ret, mdd, tr, tk = r
                    results.append((ret, mdd, risk, tk, tr))
                    print(f'{",".join(tk):25s} risk={risk:2d}%  ret={ret:>+5.1f}%  mdd={mdd:>5.2f}%  tr={tr}', flush=True)
    
    print(f'\n=== TOP 10 by RETURN (MTM MDD <= 20%) ===')
    results.sort(key=lambda x: x[0], reverse=True)
    for ret, mdd, risk, tk, tr in results[:10]:
        print(f'{",".join(tk):25s} risk={risk:2d}%  ret={ret:>+5.1f}%  mdd={mdd:>5.2f}%  tr={tr}')
