#!/usr/bin/env python3 -u
"""IR fixed and tested on FINAM MT5 M1 data."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
from strategies.impulse_return.prod.engine import check_signal as ir_check
from importlib import reload

# ── Fix: add cooldown to IR engine ──
import strategies.impulse_return.prod.engine as ir_engine

# Fix 1: Add cooldown state
ir_engine._cooldown_state = {}
orig_check = ir_engine.check_signal

def fixed_check(bar_data, ticker, params=None):
    # Fix 3: min_vol check
    vol = bar_data.get('vol', 0)
    if vol <= 0:
        return None
    
    # Fix 2: proper median_vol handling
    if params is None:
        params = ir_engine.get_default_params()
    else:
        default = ir_engine.get_default_params()
        for k, v in default.items():
            if k not in params:
                params[k] = v
    
    # Fix 1: cooldown
    cooldown = params.get('cooldown', 24)
    ticker_state = ir_engine._cooldown_state.get(ticker, 0)
    if ticker_state > 0:
        ir_engine._cooldown_state[ticker] = ticker_state - 1
        return None
    
    sig = orig_check(bar_data, ticker, params)
    if sig:
        ir_engine._cooldown_state[ticker] = cooldown
    return sig

ir_engine.check_signal = fixed_check

SPECS = {
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GD': {'ms': 0.05, 'sp': 1.0, 'go': 41942.5},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
}

def resample_to_m5(ticker, cutoff='2025-07-16'):
    """Load M1 from mt5_continuous and resample to M5 with vol."""
    ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
    rows = ch.query(f"""
        SELECT toStartOfInterval(bt, INTERVAL 5 MINUTE) as bt5,
               argMin(opn, bt) as opn,
               max(hi) as hi, min(lo) as lo,
               argMax(prc, bt) as prc,
               sum(vol) as vol
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


print('=== IR on FINAM MT5 M1 (resampled to M5) ===')
print('ticker  bars   tr    wr%    pf     mdd    ret%')

for ticker in ['CR','GD','GZ','RN','Si']:
    df = resample_to_m5(ticker)
    if df.empty or len(df) < 500:
        print(f'{ticker}: no data'); continue
    
    engine = PortfolioEngine(
        [('impulse_return', fixed_check, [ticker], None)],
        broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
    engine.executor.load_portfolio()
    result = engine.run({ticker: df}, ticker_specs={ticker: SPECS[ticker]})
    
    trades = result.trades
    n = len(trades)
    if n == 0: print(f'{ticker}: 0 trades'); continue
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100
    pf = sum(wins)/sum(abs(p) for p in losses) if losses else float('inf')
    cap = 200000; peak_cash = cap; cash_mdd = 0
    for t in trades:
        cap += t.pnl; peak_cash = max(peak_cash, cap)
        cash_mdd = max(cash_mdd, (peak_cash - cap) / peak_cash * 100)
    ret = (cap-200000)/200000*100
    print(f'{ticker}: {len(df):5d}  {n:5d}  {wr:5.1f}%  {pf:5.2f}  {cash_mdd:5.2f}%  {ret:+7.1f}%')
