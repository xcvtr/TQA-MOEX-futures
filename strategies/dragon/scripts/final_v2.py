#!/usr/bin/env python3 -u
"""Финальный портфель — alloc, GO, reinvest, risk 4%."""
import sys
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC, CAPITAL, KNUR = 4, 200000, 0.7

SPECS = {
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 34328.0},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 3643.44},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 5796.22},
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 4330.42},
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 20519.04},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 7695.02},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 34834.04},
    'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 30706.70},
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
    return bars


def portfolio(risk_pct=4):
    tickers = list(SPECS.keys())
    N = len(tickers)
    all_bars = {}
    for t in tickers:
        b = load_bars(t)
        if len(b) > 100: all_bars[t] = b
    tickers = list(all_bars.keys())
    min_len = min(len(all_bars[t]) for t in tickers)
    
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    positions = {}
    m5_cache = {t: [] for t in tickers}
    trade_log = []
    
    ticker_eq = {t: CAPITAL / N for t in tickers}  # равный аллок
    total_eq = CAPITAL
    peak_eq = CAPITAL
    mdd = 0
    
    # GO tracker
    go_used = 0
    go_limit = CAPITAL * KNUR  # 140K с пониженным ГО

    for i in range(30, min_len):
        if i % 5 == 0:
            for t in tickers:
                g = all_bars[t][i-5:i]
                if len(g) >= 3:
                    m5_cache[t].append({'opn': g[0]['opn'], 'hi': max(b['hi'] for b in g),
                                        'lo': min(b['lo'] for b in g), 'prc': g[-1]['prc']})
        
        # TICK
        for t in tickers:
            pos = positions.get(t)
            if pos is None: continue
            bar = all_bars[t][i]; ep = pos['ep']
            ms, sp = SPECS[t]['ms'], SPECS[t]['sp']
            ex = None
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
                ticker_eq[t] += pnl
                total_eq += pnl
                peak_eq = max(peak_eq, total_eq)
                mdd = max(mdd, (peak_eq-total_eq)/peak_eq*100)
                trade_log.append((i, t, pnl, pos['contracts']))
                go_used -= SPECS[t]['go'] * pos['contracts']
                del positions[t]
        
        # DETECT
        if i % 5 == 0:
            for t in tickers:
                if t in positions: continue
                if len(m5_cache[t]) < 6: continue
                bar = all_bars[t][i]
                ms, sp, go = SPECS[t]['ms'], SPECS[t]['sp'], SPECS[t]['go']
                sig = check_signal({'prc': m5_cache[t][-110:][-1]['prc'], 'bars_list': m5_cache[t][-110:]}, t, dp)
                if not sig: continue
                entry = sig['entry_price']
                
                # Размер позиции: риск от аллока тикера
                alloc = ticker_eq[t]
                risk_alloc = alloc * (risk_pct / 100)
                sl_cost = entry * sl / ms * sp + TC
                c = max(1, int(risk_alloc / sl_cost)) if sl_cost > 0 else 1
                
                # GO check
                new_go = go * c
                if go_used + new_go > go_limit:
                    continue  # не влезаем по ГО
                
                positions[t] = {'bi': i, 'ep': entry, 'dir': sig['direction'],
                                'tr': False, 'tl': None, 'contracts': c}
                go_used += new_go
    
    # Close remaining
    for t, pos in list(positions.items()):
        bar = all_bars[t][-1]; ms, sp = SPECS[t]['ms'], SPECS[t]['sp']
        raw = ((bar['prc']-pos['ep'])/ms*sp - TC) * pos['contracts']
        pnl = raw if pos['dir']=='long' else -raw
        total_eq += pnl; trade_log.append((min_len, t, pnl, pos['contracts']))
    
    # Report
    pnls = [x[2] for x in trade_log]
    n = len(pnls)
    if n == 0: return
    wins = [p for p in pnls if p > 0]
    total = sum(pnls)
    pf = sum(wins)/sum(abs(p) for p in pnls if p<=0) if any(p<=0 for p in pnls) else float('inf')
    ret = (total_eq-CAPITAL)/CAPITAL*100
    
    for t in tickers:
        tp = [x[2] for x in trade_log if x[1]==t]
        nt = len(tp)
        if nt == 0: continue
        tw = sum(p for p in tp if p>0); tl = sum(abs(p) for p in tp if p<=0)
        avg_c = sum(x[3] for x in trade_log if x[1]==t)/max(nt,1)
        print(f'  {t:4s}  | {nt:4d} tr  avg={avg_c:4.1f} cont  pnl={sum(tp):+9.0f}  pf={tw/tl if tl else 0:.2f}', flush=True)
    
    print(f'  {"="*58}')
    print(f'  PORTFOLIO: {CAPITAL:,} -> {total_eq:,.0f} ({ret:+.1f}%) | {n} tr')
    print(f'  PF: {pf:.2f}  MDD: {mdd:.2f}%  Calmar: {ret/mdd if mdd>0 else 0:.1f}')
    print(f'  Risk: {risk_pct}%/ticker  KNUR={KNUR}  GO limit={go_limit:,.0f}')


if __name__ == '__main__':
    for rp in [3, 4, 5]:
        print(f'\n=== Risk {rp}% per ticker ===', flush=True)
        portfolio(rp)
