#!/usr/bin/env python3 -u
"""Sweep с кастомной аллокацией и GO×0.5, KNUR=0.5."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies/dragon')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures/strategies')
from dragon.prod.engine import check_signal

TC, CAPITAL = 4, 200000

SPECS = {
    'BR': {'ms': 0.01, 'sp': 7.70611, 'go': 17164.0},
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'MM': {'ms': 0.05, 'sp': 0.5, 'go': 2165.21},
    'NG': {'ms': 0.001, 'sp': 7.70611, 'go': 10259.52},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
    'SV': {'ms': 0.01, 'sp': 7.70611, 'go': 15353.35},
}  # GO уже разделён на 2

ALLOC = {
    'BR': 0.05, 'NG': 0.15, 'RN': 0.15, 'SV': 0.05,
    'MM': 0.15, 'Si': 0.03, 'CR': 0.22, 'GZ': 0.20,
}

KNUR = 0.5
GO_LIMIT = CAPITAL * KNUR  # 100K

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


def run(risk_pct):
    tickers = list(ALLOC.keys())
    all_bars = {}
    for t in tickers:
        b = load_bars(t)
        if len(b) > 100: all_bars[t] = b
    tickers = list(all_bars.keys())
    min_len = min(len(all_bars[t]) for t in tickers)
    
    dp = {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100}
    ta, tt, sl = 0.015, 0.005, 0.01
    
    positions, m5_cache = {}, {t: [] for t in tickers}
    trade_log = []
    ticker_eq = {t: CAPITAL * ALLOC[t] for t in tickers}
    total_eq, peak_eq, mdd = CAPITAL, CAPITAL, 0
    go_used = 0
    
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
            ms, sp = SPECS[t]['ms'], SPECS[t]['sp']; ex = None
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
                go_used -= SPECS[t]['go'] * pos['contracts']
                del positions[t]
        if i % 5 == 0:
            for t in tickers:
                if t in positions or len(m5_cache[t]) < 6: continue
                ms, sp, go = SPECS[t]['ms'], SPECS[t]['sp'], SPECS[t]['go']
                sig = check_signal({'prc': m5_cache[t][-110:][-1]['prc'], 'bars_list': m5_cache[t][-110:]}, t, dp)
                if not sig: continue
                entry = sig['entry_price']
                risk_alloc = ticker_eq[t] * (risk_pct / 100)
                sl_cost = entry * sl / ms * sp + TC
                c = max(1, int(risk_alloc / sl_cost)) if sl_cost > 0 else 1
                if go_used + go * c > GO_LIMIT: continue
                positions[t] = {'bi': i, 'ep': entry, 'dir': sig['direction'],
                                'tr': False, 'tl': None, 'contracts': c}
                go_used += go * c
    
    for t, pos in list(positions.items()):
        bar = all_bars[t][-1]; ms, sp = SPECS[t]['ms'], SPECS[t]['sp']
        raw = ((bar['prc']-pos['ep'])/ms*sp - TC) * pos['contracts']
        pnl = raw if pos['dir']=='long' else -raw
        total_eq += pnl; trade_log.append(pnl)
    
    n = len(trade_log)
    if n == 0: return None
    wins = [p for p in trade_log if p > 0]
    total = sum(trade_log)
    pf = sum(wins)/sum(abs(p) for p in trade_log if p<=0) if any(p<=0 for p in trade_log) else float('inf')
    ret = (total_eq-CAPITAL)/CAPITAL*100
    return {'risk': risk_pct, 'ret': ret, 'mdd': mdd, 'pf': pf, 'n': n, 'final': total_eq, 'calmar': ret/mdd if mdd>0 else 0}


if __name__ == '__main__':
    print(f'GO_LIMIT={GO_LIMIT:,} (KNUR={KNUR})  ГО×0.5', flush=True)
    print('alloc: BR=5% NG=15% RN=15% SV=5% MM=15% Si=3% CR=22% GZ=20%', flush=True)
    print('risk%  ret%   MDD%    PF    Calmar   Final   Trades', flush=True)
    for rp in [3, 5, 7, 10, 12, 15, 17, 20, 25]:
        res = run(rp)
        if res:
            mark = ' <--' if 15 <= res['mdd'] <= 20 else ''
            print(f'  {res["risk"]:3d}%  {res["ret"]:>+5.1f}%  {res["mdd"]:>5.1f}%  {res["pf"]:.2f}  {res["calmar"]:>5.1f}  {res["final"]:>8.0f}  {res["n"]:>5d}{mark}', flush=True)
