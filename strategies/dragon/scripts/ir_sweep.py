#!/usr/bin/env python3 -u
"""IR sweep with reinvest and MTM MDD tracking."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
from strategies.impulse_return.prod.engine import check_signal as ir_check
import strategies.common.executor as exec_module

SPECS = {
    'CR': {'ms': 0.001, 'sp': 1.0, 'go': 1821.72},
    'GD': {'ms': 0.05, 'sp': 1.0, 'go': 41942.5},
    'GZ': {'ms': 1.0, 'sp': 1.0, 'go': 2898.11},
    'RN': {'ms': 1.0, 'sp': 1.0, 'go': 3847.51},
    'Si': {'ms': 1.0, 'sp': 1.0, 'go': 17417.02},
}

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_data = {}
for ticker, asset in [('CR','CNY'),('GD','GOLD'),('GZ','GAZR'),('RN','ROSN'),('Si','Si')]:
    q = ("SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt, "
         "argMax(pr_open,SYSTIME) as opn, argMax(pr_high,SYSTIME) as hi, "
         "argMax(pr_low,SYSTIME) as lo, argMax(pr_close,SYSTIME) as prc, "
         "sum(vol_b) as vb, sum(vol_s) as vs "
         f"FROM moex.tradestats_fo WHERE asset_code = '{asset}' "
         "AND SYSTIME >= '2025-07-16' GROUP BY bt ORDER BY bt")
    df = ch.query_df(q)
    if df.empty: continue
    df = df[df['bt'].dt.hour >= 15].copy()
    df['vol'] = (df['vb'] + df['vs']).clip(1)
    all_data[ticker] = df
ch.close()

print('risk%  ret%     cashMDD mtmMDD  PF     Trades')
for risk_pct in [1, 2, 3, 5, 7, 10, 15, 20, 25, 30]:
    exec_module.RISK_PCT = risk_pct / 100.0
    engine = PortfolioEngine(
        [('impulse_return', ir_check, list(all_data.keys()), None)],
        broker=BrokerSim(), capital=200000)
    engine.executor.load_portfolio()
    for key in list(engine.executor._portfolio.keys()):
        engine.executor._portfolio[key]['contracts'] = None
    result = engine.run(all_data, ticker_specs=SPECS)
    trades = result.trades
    n = len(trades)
    if n == 0: continue
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100
    pf = sum(wins)/sum(abs(p) for p in losses) if losses else float('inf')
    cap = 200000; peak_cash = cap; cash_mdd = 0
    for t in trades:
        cap += t.pnl; peak_cash = max(peak_cash, cap)
        cash_mdd = max(cash_mdd, (peak_cash - cap) / peak_cash * 100)
    ret = (cap-200000)/200000*100
    mtm_mdd = getattr(result, 'mtm_max_dd', 0)
    mark = ' <--' if mtm_mdd <= 20 else ''
    print(f'{risk_pct:3d}%  {ret:>+8.1f}%  {cash_mdd:>6.2f}%  {mtm_mdd:>6.2f}%  {pf:>5.2f}  {n}{mark}')
