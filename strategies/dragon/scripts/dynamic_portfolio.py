#!/usr/bin/env python3 -u
"""Динамический портфель — тикеры добавляются по мере роста equity."""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC = 4
KNUR = 0.5
CAPITAL = 200000

# Все кандидаты с GO/2 и specs
ALL_TICKERS = {
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 10259.52},
    'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 15353.35},
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 17164.0},
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
    'AF': {'ms': 10.0, 'sp': 1.0, 'go': 1010.0},   # GO/2
    'HY': {'ms': 10.0, 'sp': 1.0, 'go': 466.0},
    'VB': {'ms': 10.0, 'sp': 1.0, 'go': 1859.5},
    'TT': {'ms': 1.0, 'sp': 1.0, 'go': 6568.0},
    'MX': {'ms': 10.0, 'sp': 25.0, 'go': 3712.5},
}


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
    if bars:
        return bars
    return None


def select_portfolio(equity, risk_pct=7):
    """Выбрать тикеры по приоритету PF, минимум 2 контракта."""
    go_limit = equity * KNUR
    
    # Приоритет: PF → GO (сначала лучшие результаты)
    priority = ['NG', 'SV', 'BR', 'MM', 'RN', 'CR', 'GZ', 'Si', 'AF', 'HY', 'VB', 'TT', 'MX']
    
    selected = []
    for ticker in priority:
        if ticker not in ALL_TICKERS:
            continue
        spec = ALL_TICKERS[ticker]
        n = len(selected) + 1
        alloc_per_ticker = go_limit / n
        if spec['go'] * 2 <= alloc_per_ticker:
            selected.append(ticker)
    
    if len(selected) < 4:
        selected = ['NG', 'SV', 'BR', 'MM']
    
    alloc = {t: 1.0 / len(selected) for t in selected}
    return selected, alloc, go_limit / len(selected)


def portfolio(risk_pct=7, reinvest=True):
    selected, alloc, _ = select_portfolio(CAPITAL, risk_pct)
    
    all_bars = {}
    for t in selected:
        b = load_bars(t)
        if b: all_bars[t] = b
    tickers = list(all_bars.keys())
    if not tickers: return None
    min_len = min(len(all_bars[t]) for t in tickers)
    
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    positions, m5_cache = {}, {t: [] for t in tickers}
    trade_log = []
    ticker_eq = {t: CAPITAL * alloc[t] for t in tickers}
    total_eq, peak_eq, mdd = CAPITAL, CAPITAL, 0
    go_used = 0
    go_limit = CAPITAL * KNUR
    next_trade = 0
    
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
                ticker_eq[t] += pnl; total_eq += pnl
                peak_eq = max(peak_eq, total_eq)
                mdd = max(mdd, (peak_eq-total_eq)/peak_eq*100)
                trade_log.append(pnl)
                go_used -= ALL_TICKERS[t]['go'] * pos['contracts']
                del positions[t]
        if i % 5 == 0:
            # Раз в день пересчитываем портфель при росте equity
            if reinvest and i > next_trade + 200:
                go_limit = total_eq * KNUR
                selected, alloc, _ = select_portfolio(total_eq, risk_pct)
                next_trade = i
                for t in tickers:
                    if t in alloc:
                        ticker_eq[t] = total_eq * alloc[t]
            for t in tickers:
                if t in positions or len(m5_cache[t]) < 6: continue
                ms, sp, go = ALL_TICKERS[t]['ms'], ALL_TICKERS[t]['sp'], ALL_TICKERS[t]['go']
                sig = check_signal({'prc': m5_cache[t][-110:][-1]['prc'], 'bars_list': m5_cache[t][-110:]}, t, dp)
                if not sig: continue
                entry = sig['entry_price']
                risk_alloc = ticker_eq[t] * (risk_pct / 100)
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
        total_eq += pnl; trade_log.append(pnl)
    
    n = len(trade_log)
    wins = [p for p in trade_log if p > 0]
    total = sum(trade_log)
    pf = sum(wins)/sum(abs(p) for p in trade_log if p<=0) if any(p<=0 for p in trade_log) else float('inf')
    ret = (total_eq-CAPITAL)/CAPITAL*100
    
    print(f'  ТИКЕРЫ: {", ".join(tickers)}', flush=True)
    for t in tickers:
        print(f'  {t:4s}', end='', flush=True)
    print()
    print(f'  Капитал: {CAPITAL:,} -> {total_eq:,.0f} ({ret:+.1f}%) | {n} tr')
    print(f'  PF: {pf:.2f} | MDD: {mdd:.2f}% | Calmar: {ret/mdd if mdd>0 else 0:.1f}')
    return total_eq


if __name__ == '__main__':
    print('=== ДИНАМИЧЕСКИЙ ПОРТФЕЛЬ — тикеры по мере роста equity ===', flush=True)
    print('', flush=True)
    for rp in [5, 7, 10]:
        print(f'\n--- Risk {rp}% ---', flush=True)
        portfolio(risk_pct=rp)
