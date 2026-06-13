#!/usr/bin/env python3
"""Backtest топ-паттернов: walk-forward, комиссии, портфель."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

CAPITAL = 100_000
COMM = 4  # round-trip per contract
HOLD = 5

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

# Контрактные размеры (cs) для интересующих тикеров
CS_MAP = {
    'SV': 1, 'SF': 1000, 'CE': 100, 'PT': 10, 'W4': 1, 'RL': 1,
    'MY': 1, 'OJ': 10, 'BM': 10, 'NR': 1, 'TT': 1, 'GD': 10,
    'PD': 10, 'AF': 100, 'HY': 1000, 'ME': 100, 'RB': 1000,
    'MM': 1, 'GL': 10, 'NA': 1, 'MX': 1, 'RI': 10,
    'YD': 100, 'NG': 100, 'YB': 1, 'EURRUBF': 1000,
}

# Паттерны для теста
PATTERNS = {
    'vol_up_yb_down_fiz_up': lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb < 0 and dfn > 0,
    'vol_up_oi_up_yb_up':     lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi > 0 and dyb > 0,
    'vol_up_yb_up_fiz_down':  lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dyb > 0 and dfn < 0,
    'vol_up_oi_down':         lambda dv, dyb, dys, dfn, dtoi: dv > 0 and dtoi < 0,
    'fiz_extreme_vol_up':     lambda dv, dyb, dys, dfn, dtoi: dv > 0 and abs(dfn) > 5,
}

# Тикеры для каждого паттерна (из предыдущего скрипта)
TICKER_SETS = {
    'vol_up_yb_down_fiz_up': ['RL','MY','TT','MN','W4','HY','SP','RB','AF','NG','CC','OJ','PD'],
    'vol_up_oi_up_yb_up':     ['RL','CE','SV','GL','NA','MX','MM','NG','SP','NR','ME','OJ','TT'],
    'vol_up_yb_up_fiz_down':  ['CE','OJ','W4','PT','MM','SP','SV','GD','MY','AF','HY','TT'],
    'vol_up_oi_down':         ['W4','CE','MY','TT','RB','RL','SV','GD','PT'],
    'fiz_extreme_vol_up':     ['SV','PT','PD','ME','MY','W4','RB','YD'],
}

def get_daily_data(ticker):
    rows = ch.query("""
        SELECT toDate(p.time) as d,
               argMax(p.open, p.time) as open,
               argMax(p.high, p.time) as high,
               argMax(p.low, p.time) as low,
               argMax(p.close, p.time) as close,
               argMax(p.volume, p.time) as volume,
               argMax(o.yur_buy, p.time) as yur_buy,
               argMax(o.yur_sell, p.time) as yur_sell,
               argMax(o.fiz_buy, p.time) as fiz_buy,
               argMax(o.fiz_sell, p.time) as fiz_sell,
               argMax(o.total_oi, p.time) as total_oi
        FROM moex.prices_5m p
        INNER JOIN moex.prices_5m_oi o ON p.symbol = o.symbol AND p.time = o.time
        WHERE p.symbol = %(t)s AND p.time >= '2024-01-01' AND p.time <= '2026-05-01'
        GROUP BY d ORDER BY d
    """, parameters={'t': ticker}).result_rows
    
    if len(rows) < 60:
        return None
    
    dates = [str(r[0]) for r in rows]
    opn = np.array([r[1] for r in rows], dtype=float)
    high = np.array([r[2] for r in rows], dtype=float)
    low = np.array([r[3] for r in rows], dtype=float)
    close = np.array([r[4] for r in rows], dtype=float)
    vol = np.array([r[5] for r in rows], dtype=float)
    yb = np.array([r[6] for r in rows], dtype=float)
    ys = np.array([r[7] for r in rows], dtype=float)
    fb = np.array([r[8] for r in rows], dtype=float)
    fs = np.array([r[9] for r in rows], dtype=float)
    toi = np.array([r[10] for r in rows], dtype=float)
    toi = np.where(toi <= 0, 1, toi)
    
    # Нормированные изменения
    v_mean = np.mean(vol) + 1
    yb_mean = np.mean(yb) + 1
    toi_mean = np.mean(toi) + 1
    
    dv = np.diff(vol) / v_mean
    dyb = np.diff(yb) / yb_mean
    dys = np.diff(ys) / yb_mean
    dtoi = np.diff(toi) / toi_mean
    fiz_net = (fb - fs) / toi * 100
    dfn = np.diff(fiz_net)
    
    return {
        'dates': dates, 'opn': opn, 'high': high, 'low': low, 'close': close,
        'dv': dv, 'dyb': dyb, 'dys': dys, 'dfn': dfn, 'dtoi': dtoi,
        'n': len(rows)
    }

def backtest_wf(ticker, cs, pfunc, label):
    d = get_daily_data(ticker)
    if d is None:
        return None
    
    n = d['n']
    nf = 4
    fsize = n // nf
    
    fold_results = []
    
    for f in range(nf):
        s = f * fsize
        e = n if f == 4 else (f + 1) * fsize
        
        eq = CAPITAL
        peak = eq
        mdd = 0
        trades = []
        
        for i in range(s, min(e, n - HOLD - 2)):
            if i >= len(d['dv']):
                break
            if not pfunc(d['dv'][i], d['dyb'][i], d['dys'][i], d['dfn'][i], d['dtoi'][i]):
                continue
            
            ei = i + 1
            xi = min(ei + HOLD, n - 2)
            if ei >= n - 2:
                continue
            
            ep = float(d['opn'][ei])
            xp = float(d['close'][xi])
            go = ep * cs
            nc = max(1, int(eq // go)) if go > 0 else 1
            gp = nc * cs * (xp - ep)
            cm = nc * COMM
            npnl = gp - cm
            eq += npnl
            
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            mdd = max(mdd, dd)
            trades.append(npnl)
        
        ret = (eq - CAPITAL) / CAPITAL * 100
        wins = sum(1 for p in trades if p > 0)
        wr = wins / len(trades) * 100 if trades else 0
        calmar = ret / mdd if mdd > 0 else 0
        
        fold_results.append({
            'fold': f+1, 'trades': len(trades),
            'ret': round(ret, 1), 'dd': round(mdd, 1),
            'wr': round(wr, 0), 'calmar': round(calmar, 2)
        })
    
    return fold_results

print('=== WALK-FORWARD BACKTEST (4 folds, HOLD=5, комиссии) ===')
print()

all_best = []

for pname, pfunc in PATTERNS.items():
    tickers = TICKER_SETS[pname]
    print(f'--- {pname} ---')
    
    for ticker in tickers:
        cs = CS_MAP.get(ticker, 1)
        fr = backtest_wf(ticker, cs, pfunc, pname)
        if not fr:
            continue
        
        rets = [f['ret'] for f in fr]
        dds = [f['dd'] for f in fr]
        wrs = [f['wr'] for f in fr]
        trs = [f['trades'] for f in fr]
        mean_ret = np.mean(rets)
        mean_dd = np.mean(dds)
        mean_wr = np.mean(wrs)
        min_ret = min(rets)
        neg_folds = sum(1 for r in rets if r < 0)
        
        status = '✅' if (mean_ret > 3 and neg_folds <= 1 and mean_dd < 15) else '—'
        
        print(f'  {ticker:>6}: rets={rets} dd={[f["dd"] for f in fr]} mean_ret={mean_ret:+5.1f}% mean_dd={mean_dd:4.1f}% mean_wr={mean_wr:.0f}% neg={neg_folds} {status}')
        
        if mean_ret > 3 and neg_folds <= 1 and mean_dd < 15:
            all_best.append((pname, ticker, mean_ret, mean_dd, mean_wr, neg_folds))

print()
print('=== BEST BY PATTERN ===')
for pname in PATTERNS:
    pbest = [x for x in all_best if x[0] == pname]
    pbest.sort(key=lambda x: -x[2])
    if pbest:
        print(f'{pname}:')
        for _, t, r, d, w, nf in pbest[:3]:
            print(f'  {t}: ret={r:+5.1f}% DD={d:.1f}% WR={w:.0f}% neg={nf}')

# Сохраняем
with open('reports/pattern_backtest.json', 'w') as f:
    json.dump(all_best, f, indent=2, default=str)
print(f'\nSaved to reports/pattern_backtest.json')
