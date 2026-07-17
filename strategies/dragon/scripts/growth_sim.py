#!/usr/bin/env python3 -u
"""Симуляция роста портфеля — equity растёт, добавляются тикеры."""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC = 4
KNUR = 0.5
START_EQ = 200000
RISK = 12

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

PRIORITY = ['NG', 'SV', 'BR', 'MM', 'RN', 'CR', 'GZ', 'Si', 'AF', 'HY', 'VB', 'TT', 'MX']


def load_bars(ticker):
    import clickhouse_connect as cc
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    rows = ch.query(f"SELECT bt, opn, hi, lo, prc FROM moex.mt5_continuous WHERE ticker='{ticker}' AND bt >= '2025-07-16' ORDER BY bt").result_rows
    ch.close()
    bars = []
    for r in rows:
        ts = r[0]
        if ts.weekday() >= 5: continue
        h, m = ts.hour, ts.minute
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4])})
    return bars if bars else None


def select(equity):
    go_limit = equity * KNUR
    selected = []
    for t in PRIORITY:
        spec = ALL_TICKERS[t]
        n = max(len(selected), 1)
        alloc = go_limit / n
        if spec['go'] * 2 <= alloc and t not in selected:
            selected.append(t)
            alloc = go_limit / len(selected)
    if len(selected) < 4:
        selected = ['NG', 'SV', 'BR', 'MM']
    alloc = {t: 1.0/len(selected) for t in selected}
    return selected, alloc


def simulate():
    equity = START_EQ
    peak = equity
    mdd = 0
    total_trades = 0
    
    print(f'Год | Капитал  | Доход | MDD  | Тикеры')
    print(f'----+----------+-------+------+-------------------')
    
    selected, alloc = select(equity)
    print(f' 0  | {equity:>6.0f}K |   —   |  —   | {",".join(selected[:6])}', flush=True)
    
    for year in range(1, 6):
        year_start = equity
        year_peak = equity
        
        # Load bars for selected tickers
        tickers = list(selected)
        all_bars = {}
        for t in tickers:
            b = load_bars(t)
            if b: all_bars[t] = b
        
        tickers = list(all_bars.keys())
        if not tickers:
            break
        
        min_len = min(len(all_bars[t]) for t in tickers)
        dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
        ta, tt, sl = 0.015, 0.005, 0.01
        
        positions, m5_cache = {}, {t: [] for t in tickers}
        go_used = 0
        go_limit = equity * KNUR
        ticker_eq = {t: equity * alloc[t] for t in tickers}
        year_trades = 0
        
        for i in range(30, min_len):
            if i % 5 == 0:
                for t in tickers:
                    g = all_bars[t][i-5:i]
                    if len(g) >= 3:
                        m5_cache[t].append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                                            'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
            for t in tickers:
                pos = positions.get(t)
                if pos is None: continue
                bar = all_bars[t][i]; ep = pos['ep']
                ms, sp = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp']; ex = None
                slev = ep*(1-sl) if pos['dir']=='long' else ep*(1+sl)
                if (pos['dir']=='long' and bar['lo']<=slev) or (pos['dir']=='short' and bar['hi']>=slev): ex=slev
                if not ex and i%5==4:
                    if not pos.get('tr'):
                        if (pos['dir']=='long' and bar['hi']>=ep*(1+ta)) or (pos['dir']=='short' and bar['lo']<=ep*(1-ta)):
                            pos['tr']=True; pos['tl']=bar['hi']*(1-tt) if pos['dir']=='long' else bar['lo']*(1+tt)
                    if pos.get('tr'):
                        if (pos['dir']=='long' and bar['lo']<=pos['tl']) or (pos['dir']=='short' and bar['hi']>=pos['tl']): ex=pos['tl']
                if not ex and i-pos['bi']>=60: ex=bar['prc']
                if ex is not None:
                    raw = ((ex-ep)/ms*sp - TC) * pos['contracts']
                    pnl = raw if pos['dir']=='long' else -raw
                    ticker_eq[t] += pnl; equity += pnl
                    year_peak = max(year_peak, equity)
                    mdd = max(mdd, (year_peak-equity)/year_peak*100)
                    go_used -= ALL_TICKERS[t]['go'] * pos['contracts']
                    year_trades += 1; total_trades += 1
                    del positions[t]
            if i % 5 == 0:
                go_limit = equity * KNUR
                selected, alloc = select(equity)
                for t in selected:
                    if t not in ticker_eq:
                        ticker_eq[t] = equity * alloc.get(t, 1/len(selected))
                for t in tickers:
                    ticker_eq[t] = equity * alloc.get(t, 1/len(selected))
                for t in tickers:
                    if t in positions or len(m5_cache[t]) < 6: continue
                    ms, sp, go = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp'], ALL_TICKERS[t]['go']
                    sig = check_signal({'prc': m5_cache[t][-110:][-1]['prc'], 'bars_list': m5_cache[t][-110:]}, t, dp)
                    if not sig: continue
                    entry = sig['entry_price']
                    risk_alloc = ticker_eq.get(t, equity/len(tickers)) * (RISK / 100)
                    sl_cost = entry * sl / ms * sp + TC
                    c = max(1, int(risk_alloc / sl_cost)) if sl_cost > 0 else 1
                    if go_used + go * c > go_limit: continue
                    positions[t] = {'bi': i, 'ep': entry, 'dir': sig['direction'],
                                    'tr': False, 'tl': None, 'contracts': c}
                    go_used += go * c
        
        for t, pos in list(positions.items()):
            bar = all_bars[t][-1]; ms, sp = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp']
            raw = ((bar['prc']-pos['ep'])/ms*sp - TC) * pos['contracts']
            pnl = raw if pos['dir']=='long' else -raw
            equity += pnl
        
        ret = (equity - year_start) / year_start * 100
        tickers_str = ",".join([t for t in PRIORITY if t in alloc])
        print(f' {year:2d}  | {equity:>6.0f} | {ret:>+5.1f}% | {mdd:.1f}% | {tickers_str}', flush=True)
        
        if equity > year_start * 5:
            break  # exit simulation, extrapolation unreliable


if __name__ == '__main__':
    print(f'СИМУЛЯЦИЯ РОСТА ПОРТФЕЛЯ (risk={RISK}%, KNUR={KNUR}, ГО×0.5)', flush=True)
    print(f'Старт: {START_EQ:,}₽', flush=True)
    print('', flush=True)
    simulate()
