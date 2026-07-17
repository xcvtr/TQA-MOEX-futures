#!/usr/bin/env python3 -u
"""Полный бэктест 2020-2026 — динамический портфель, MTM MDD."""
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
PRIORITY = ['NG','SV','BR','MM','RN','CR','GZ','Si','AF','HY','VB','TT','MX']


def load_bars(ticker, cutoff='2020-01-01'):
    import clickhouse_connect as cc
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    rows = ch.query(f"SELECT bt, opn, hi, lo, prc FROM moex.mt5_continuous WHERE ticker='{ticker}' AND bt >= '{cutoff}' ORDER BY bt").result_rows
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
        if spec['go'] * 2 <= go_limit / n:
            selected.append(t)
    if len(selected) < 4:
        selected = ['NG','SV','BR','MM']
    alloc = {t: 1.0/len(selected) for t in selected}
    return selected, alloc


def backtest():
    equity = float(START_EQ)
    peak_eq = equity
    mtm_mdd = 0
    
    selected, alloc = select(equity)
    tickers = list(selected)
    
    all_bars = {}
    for t in tickers:
        b = load_bars(t, '2020-01-01')
        if b and len(b) > 5000:
            all_bars[t] = b
    
    tickers = list(all_bars.keys())
    min_len = min(len(all_bars[t]) for t in tickers)
    
    print(f'Данных: {len(tickers)} тикеров, {min_len} баров, с 2020', flush=True)
    print(f'Старт: {tickers}', flush=True)
    
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    positions, m5_cache = {}, {t: [] for t in tickers}
    go_used = 0
    go_limit = equity * KNUR
    ticker_eq = {t: equity * alloc[t] for t in tickers}
    trade_log = []
    total_trades = 0
    
    for i in range(30, min_len):
        # Update M5 cache
        if i % 5 == 0:
            for t in tickers:
                g = all_bars[t][i-5:i]
                if len(g) >= 3:
                    m5_cache[t].append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                                        'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
        
        # TICK: check all positions
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
                go_used -= ALL_TICKERS[t]['go'] * pos['contracts']
                total_trades += 1
                trade_log.append((bar['ts'], equity, 'close'))
                del positions[t]
        
        # MTM MDD: equity + unrealized PnL
        mtm_eq = equity
        for t in tickers:
            pos = positions.get(t)
            if pos is None: continue
            bar = all_bars[t][i]; ep = pos['ep']
            ms, sp = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp']
            unrealized = ((bar['prc'] - ep) / ms * sp) * pos['contracts']
            if pos['dir'] == 'short': unrealized = -unrealized
            mtm_eq += unrealized
        
        peak_eq = max(peak_eq, mtm_eq)
        mtm_mdd = max(mtm_mdd, (peak_eq - mtm_eq) / peak_eq * 100)
        
        # DETECT: new signals
        if i % 5 == 0:
            go_limit = equity * KNUR
            selected_new, alloc_new = select(equity)
            
            # Add new tickers as equity grows
            for t in selected_new:
                if t not in all_bars:
                    b = load_bars(t, '2020-01-01')
                    if b and len(b) > 5000:
                        all_bars[t] = b
                        m5_cache[t] = []
                        # Rebuild M5 cache
                        for j in range(30, i+1, 5):
                            g = all_bars[t][j-5:j]
                            if len(g) >= 3:
                                m5_cache[t].append({'opn': g[0]['opn'], 'hi': max(b2['hi'] for b2 in g),
                                                    'lo': min(b2['lo'] for b2 in g), 'prc': g[-1]['prc']})
            
            tickers = list(all_bars.keys())
            for t in tickers:
                ticker_eq[t] = equity * alloc_new.get(t, 1/len(tickers))
            
            for t in tickers:
                if t in positions or len(m5_cache.get(t, [])) < 6: continue
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
    
    # Close remaining
    for t, pos in list(positions.items()):
        bar = all_bars[t][-1]; ms, sp = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp']
        raw = ((bar['prc']-pos['ep'])/ms*sp - TC) * pos['contracts']
        pnl = raw if pos['dir']=='long' else -raw
        equity += pnl; total_trades += 1
    
    ret = (equity - START_EQ) / START_EQ * 100
    wins = len([x for x in trade_log if x[1] > 0])
    
    print(f'\nРЕЗУЛЬТАТЫ (2020-2026, {total_trades} сделок):', flush=True)
    print(f'  Старт: {START_EQ:,}  ->  Финал: {equity:,.0f}  ({ret:+.1f}%)', flush=True)
    print(f'  MTM MDD: {mtm_mdd:.2f}%', flush=True)
    print(f'  CAGR: {((equity/START_EQ)**(1/6.5)-1)*100:.1f}% годовых', flush=True)
    return equity, mtm_mdd


if __name__ == '__main__':
    print('БЭКТЕСТ 2020-2026 — ДИНАМИЧЕСКИЙ ПОРТФЕЛЬ', flush=True)
    print(f'Risk={RISK}%, KNUR={KNUR}, ГО×0.5, TC={TC}', flush=True)
    print('-'*50, flush=True)
    backtest()
