#!/usr/bin/env python3 -u
"""IR on single-contract tradestats_fo — no multi-contract jumps."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np
import clickhouse_connect as cc
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
from strategies.impulse_return.prod.engine import check_signal as ir_check, get_default_params, _cooldown_state

# With cooldown fix
def _fixed(bd, t, p=None):
    v = bd.get('vol', 0)
    if v <= 0: return None
    if p is None: p = get_default_params()
    else:
        d = get_default_params()
        for k, val in d.items():
            if k not in p: p[k] = val
    cd = _cooldown_state.get(t, 0)
    if cd > 0: _cooldown_state[t] = cd - 1; return None
    s = ir_check(bd, t, p)
    if s: _cooldown_state[t] = p.get('cooldown', 24)
    return s

# Single contracts per ticker
TICKER_CONFIG = {
    'CR': {'secid': 'CRM6', 'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GD': {'secid': 'GDM6', 'ms': 0.05, 'sp': 1.0, 'go': 41942.5},
    'GZ': {'secid': 'GZM6', 'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'RN': {'secid': 'RNM6', 'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'Si': {'secid': 'SiM6', 'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
}

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_data = {}

for ticker, cfg in TICKER_CONFIG.items():
    df = ch.query_df(f"""
        SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
               argMax(pr_open,SYSTIME) as opn, argMax(pr_high,SYSTIME) as hi,
               argMax(pr_low,SYSTIME) as lo, argMax(pr_close,SYSTIME) as prc,
               sum(vol_b) as vb, sum(vol_s) as vs
        FROM moex.tradestats_fo
        WHERE secid='{cfg['secid']}' AND SYSTIME>='2025-07-16'
          AND toHour(SYSTIME)>=15
        GROUP BY bt ORDER BY bt
    """)
    if df.empty: continue
    df['vol'] = (df['vb'] + df['vs']).clip(1)
    all_data[ticker] = df
    print(f'{ticker}: {len(df)} bars (secid={cfg["secid"]})', flush=True)

ch.close()

print('\n--- IR per ticker (single contract) ---', flush=True)
print('ticker  bars   tr    wr%    pf     mdd    ret%    avgPnl', flush=True)

for ticker, cfg in TICKER_CONFIG.items():
    if ticker not in all_data: continue
    engine = PortfolioEngine([('impulse_return', _fixed, [ticker], None)],
        broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
    engine.executor.load_portfolio()
    sp = {ticker: {'ms': cfg['ms'], 'sp': cfg['sp'], 'go': cfg['go']}}
    result = engine.run({ticker: all_data[ticker]}, ticker_specs=sp)
    trades = result.trades
    n = len(trades)
    if n == 0: print(f'{ticker}: 0 trades', flush=True); continue
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100
    pf = sum(wins)/sum(abs(p) for p in losses) if losses else float('inf')
    cap = 200000; peak = cap; cash_mdd = 0
    for t in trades: cap += t.pnl; peak = max(peak, cap); cash_mdd = max(cash_mdd, (peak-cap)/peak*100)
    ret = (cap-200000)/200000*100
    mtm_mdd = getattr(result, 'mtm_max_dd', 0)
    avg = np.mean(pnls) if pnls else 0
    print(f'{ticker}: {len(all_data[ticker]):5d} {n:4d}  {wr:5.1f}%  {pf:5.2f}  {cash_mdd:5.2f}%  {ret:+7.1f}%  {avg:+7.0f}', flush=True)

print('\n--- Full portfolio ---', flush=True)
_cooldown_state.clear()
engine = PortfolioEngine(
    [('impulse_return', _fixed, list(TICKER_CONFIG.keys()), None)],
    broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
engine.executor.load_portfolio()
sp = {t: {'ms':cfg['ms'],'sp':cfg['sp'],'go':cfg['go']} for t,cfg in TICKER_CONFIG.items()}
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
