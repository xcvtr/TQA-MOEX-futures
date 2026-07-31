#!/usr/bin/env python3 -u
"""Stop Hunt on MT5 FINAM continuous M1 (resampled to M5)."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
from strategies.stop_hunt.prod.engine import check_signal as sh_check

SPECS = {
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GD': {'ms': 0.05, 'sp': 1.0, 'go': 41942.5},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
}

TICKERS = ['CR', 'GD', 'GZ', 'RN', 'Si']

def load_mt5_m5(ticker, cutoff='2025-07-16'):
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    rows = ch.query(f"""
        SELECT toStartOfInterval(bt, INTERVAL 5 MINUTE) as bt5,
               argMin(opn, bt) as opn, max(hi) as hi, min(lo) as lo,
               argMax(prc, bt) as prc, sum(vol) as vol
        FROM moex.mt5_continuous
        WHERE ticker = '{ticker}' AND bt >= '{cutoff}'
        GROUP BY bt5 ORDER BY bt5
    """).result_rows
    ch.close()
    import pandas as pd
    df = pd.DataFrame(rows, columns=['bt', 'opn', 'hi', 'lo', 'prc', 'vol'])
    if df.empty: return df
    df['bt'] = pd.to_datetime(df['bt'], utc=True)
    df = df[df['bt'].dt.hour >= 15].copy()
    df['vol'] = df['vol'].clip(1).fillna(1)
    return df

print('=== Stop Hunt on MT5 FINAM M5 ===', flush=True)
all_data = {}
for ticker in TICKERS:
    df = load_mt5_m5(ticker)
    if df.empty or len(df) < 500: continue
    all_data[ticker] = df
    print(f'{ticker}: {len(df)} bars', flush=True)

print('\n--- Per ticker ---', flush=True)
for ticker in TICKERS:
    if ticker not in all_data: continue
    engine = PortfolioEngine([('stop_hunt', sh_check, [ticker], None)],
        broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
    engine.executor.load_portfolio()
    sp = {ticker: {k:SPECS[ticker][k] for k in ('ms','sp','go')}}
    result = engine.run({ticker: all_data[ticker]}, ticker_specs=sp)
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
    print(f'{ticker}: {len(all_data[ticker]):5d} {n:4d}  {wr:5.1f}%  {pf:5.2f}  {cash_mdd:5.2f}%  {mtm_mdd:5.2f}%  {ret:+7.1f}%', flush=True)

print('\n--- Portfolio (5 tickers, 1 contract) ---', flush=True)
engine = PortfolioEngine([('stop_hunt', sh_check, list(all_data.keys()), None)],
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
