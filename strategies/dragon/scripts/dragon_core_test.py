#!/usr/bin/env python3 -u
"""Dragon on MT5 FINAM M1 — core tickers only."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
from strategies.dragon.prod.engine import check_signal as dragon_check

SPECS = {
    'MM': {'ms': 1.0, 'sp': 1.0, 'go': 3648.84},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'NG': {'ms': 1.0, 'sp': 1.0, 'go': 3108.57},
    'BR': {'ms': 1.0, 'sp': 1.0, 'go': 16721.34},
    'SV': {'ms': 1.0, 'sp': 1.0, 'go': 4012.93},
}

TICKERS = ['MM', 'GZ', 'NG', 'BR', 'SV']

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_data = {}
for ticker in TICKERS:
    rows = ch.query(f"""
        SELECT toStartOfInterval(bt, INTERVAL 1 MINUTE) as bt1,
               argMin(opn, bt) as opn, max(hi) as hi, min(lo) as lo,
               argMax(prc, bt) as prc, sum(vol) as vol
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}' AND bt >= '2025-07-16'
        GROUP BY bt1 ORDER BY bt1
    """).result_rows
    if not rows: continue
    import pandas as pd
    df = pd.DataFrame(rows, columns=['bt','opn','hi','lo','prc','vol'])
    df['bt'] = pd.to_datetime(df['bt']).dt.tz_convert('Asia/Irkutsk')
    df = df[(df['bt'].dt.hour >= 15) | (df['bt'].dt.hour < 2)].copy()
    df['vol'] = df['vol'].clip(1).fillna(1)
    all_data[ticker] = df
    print(f'{ticker}: {len(df)} M1 bars', flush=True)
ch.close()

print('\n--- Dragon per ticker (MT5 FINAM M1) ---', flush=True)
print('tick  bars   tr    wr%    pf     mdd    mtmMDD  ret%', flush=True)

results = {}
for ticker in TICKERS:
    if ticker not in all_data: continue
    df = all_data[ticker]
    if len(df) < 1000: print(f'{ticker}: <1000 bars, skip', flush=True); continue
    
    # Dragon doesn't need reset_state — fresh engine per ticker
    engine = PortfolioEngine([('dragon', dragon_check, [ticker], None)],
        broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
    engine.executor.load_portfolio()
    sp = {ticker: {k:SPECS[ticker][k] for k in ('ms','sp','go')}}
    result = engine.run({ticker: df}, ticker_specs=sp)
    trades = result.trades
    n = len(trades)
    if n == 0: print(f'{ticker}: 0 trades', flush=True); continue
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100
    pf = sum(wins)/sum(abs(p) for p in losses) if losses else float('inf')
    cap=200000;pk=cap;cash_mdd=0
    for t in trades: cap+=t.pnl;pk=max(pk,cap);cash_mdd=max(cash_mdd,(pk-cap)/pk*100)
    ret=(cap-200000)/200000*100
    mtm_mdd=getattr(result,'mtm_max_dd',0)
    results[ticker] = {'n':n, 'wr':wr, 'pf':pf, 'cash_mdd':cash_mdd, 'mtm_mdd':mtm_mdd, 'ret':ret}
    print(f'{ticker}: {len(df):5d} {n:4d}  {wr:5.1f}%  {pf:5.2f}  {cash_mdd:5.2f}%  {mtm_mdd:5.2f}%  {ret:+7.1f}%', flush=True)

print('\n--- Portfolio (all core tickers) ---', flush=True)
engine = PortfolioEngine([('dragon', dragon_check, list(all_data.keys()), None)],
    broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
engine.executor.load_portfolio()
sp = {t: {k:SPECS[t][k] for k in ('ms','sp','go')} for t in all_data}
result = engine.run(all_data, ticker_specs=sp)
trades = result.trades; n = len(trades)
pnls = [t.pnl for t in trades]
wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
wr = len(wins)/n*100 if n else 0
pf = sum(wins)/sum(abs(p) for p in losses) if losses else float('inf')
cap=200000;pk=cap;cash_mdd=0
for t in trades: cap+=t.pnl;pk=max(pk,cap);cash_mdd=max(cash_mdd,(pk-cap)/pk*100)
ret=(cap-200000)/200000*100
mtm_mdd=getattr(result,'mtm_max_dd',0)
print(f'Portfolio: n={n} wr={wr:.1f}% pf={pf:.2f} cashMDD={cash_mdd:.2f}% mtmMDD={mtm_mdd:.2f}% ret={ret:+.1f}%', flush=True)
