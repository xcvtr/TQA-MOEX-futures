#!/usr/bin/env python3 -u
"""Dragon sweep on MT5 FINAM M1 — full ticker scan."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
import importlib

# Load dragon engine fresh
spec = importlib.util.spec_from_file_location(
    "dragon_engine", "/home/user/projects/TQA-MOEX-futures/strategies/dragon/prod/engine.py")
dragon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dragon)
dragon_check = dragon.check_signal

SPECS = {
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GD': {'ms': 0.05, 'sp': 1.0, 'go': 41942.5},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
    'MM': {'ms': 1.0, 'sp': 1.0, 'go': 3648.84},
    'NG': {'ms': 1.0, 'sp': 1.0, 'go': 3108.57},
    'BR': {'ms': 1.0, 'sp': 1.0, 'go': 16721.34},
    'SV': {'ms': 1.0, 'sp': 1.0, 'go': 4012.93},
}

# All tickers from mt5_continuous
CH_TICKERS = ['CR','GD','GZ','RN','Si','MM','NG','BR','SV']

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_data = {}
for ticker in CH_TICKERS:
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
    df = pd.DataFrame(rows, columns=['bt', 'opn', 'hi', 'lo', 'prc', 'vol'])
    df['bt'] = pd.to_datetime(df['bt']).dt.tz_convert('Asia/Irkutsk')
    # MOEX hours: 10:00-18:45 MSK = 15:00-01:45 IRK
    df = df[(df['bt'].dt.hour >= 15) | (df['bt'].dt.hour < 2)].copy()
    # Exclude lunch break 14:00-14:03 MSK = 19:00-19:03 IRK
    df = df[~((df['bt'].dt.hour == 19) & (df['bt'].dt.minute < 4))].copy()
    df['vol'] = df['vol'].clip(1).fillna(1)
    all_data[ticker] = df
    print(f'{ticker}: {len(df)} M1 bars', flush=True)
ch.close()

print('\n--- Dragon per ticker (MT5 FINAM M1) ---', flush=True)
print('tick  bars   tr    wr%    pf     mdd    mtmMDD  ret%', flush=True)

results = {}
for ticker in CH_TICKERS:
    if ticker not in all_data: continue
    df = all_data[ticker]
    if len(df) < 1000: print(f'{ticker}: <1000 bars, skip'); continue
    
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
    results[ticker] = {'n':n, 'wr':wr, 'pf':pf, 'mdd':cash_mdd, 'mtm_mdd':mtm_mdd, 'ret':ret, 'df':df}
    print(f'{ticker}: {len(df):5d} {n:4d}  {wr:5.1f}%  {pf:5.2f}  {cash_mdd:5.2f}%  {mtm_mdd:5.2f}%  {ret:+7.1f}%', flush=True)

print('\n--- Best candidates (PF>1.3, MDD<20%) ---', flush=True)
bests = {t:r for t,r in results.items() if r['pf'] > 1.3 and r['mtm_mdd'] < 20}
for t,r in sorted(bests.items(), key=lambda x: x[1]['pf'], reverse=True):
    print(f'{t}: PF={r["pf"]:.2f} ret={r["ret"]:+.1f}% mtmMDD={r["mtm_mdd"]:.2f}% tr={r["n"]}', flush=True)

print('\n--- Portfolio (all with PF>1.3) ---', flush=True)
if bests:
    best_tickers = list(bests.keys())
    best_data = {t: results[t]['df'] for t in best_tickers}
    sp = {t: {k:SPECS[t][k] for k in ('ms','sp','go')} for t in best_tickers if t in SPECS}
    engine = PortfolioEngine([('dragon', dragon_check, best_tickers, None)],
        broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
    engine.executor.load_portfolio()
    result = engine.run(best_data, ticker_specs=sp)
    trades = result.trades; n = len(trades)
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100 if n else 0
    pf = sum(wins)/sum(abs(p) for p in losses) if losses else float('inf')
    cap=200000;pk=cap;cash_mdd=0
    for t in trades: cap+=t.pnl;pk=max(pk,cap);cash_mdd=max(cash_mdd,(pk-cap)/pk*100)
    ret=(cap-200000)/200000*100
    mtm_mdd=getattr(result,'mtm_max_dd',0)
    print(f'Portfolio {best_tickers}: n={n} wr={wr:.1f}% pf={pf:.2f} cashMDD={cash_mdd:.2f}% mtmMDD={mtm_mdd:.2f}% ret={ret:+.1f}%', flush=True)
