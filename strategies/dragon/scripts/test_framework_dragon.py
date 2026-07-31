#!/usr/bin/env python3 -u
"""Dragon M1→M5 resampling test in PortfolioEngine."""
import sys, os
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import clickhouse_connect as cc, pandas as pd, psycopg2
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
from strategies.dragon.prod.engine import check_signal as dragon_check
import strategies.common.executor as exec_module
exec_module.RISK_PCT = 0.15

# PG specs
conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
cur = conn.cursor()
cur.execute("SELECT ticker, min_step, step_price, go FROM futures.ticker_specs WHERE ticker IN ('MM','GZ','NG','BR','SV')")
SPECS = {r[0]: {'ms': float(r[1]), 'sp': float(r[2]), 'go': float(r[3])} for r in cur.fetchall()}
cur.close(); conn.close()

# MT5 M1 data
ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_data = {}
for t in ['MM','GZ','NG','BR','SV']:
    q = ("SELECT toStartOfInterval(bt,INTERVAL 1 MINUTE) as bt, "
         "argMin(opn,bt) as opn, max(hi) as hi, min(lo) as lo, "
         "argMax(prc,bt) as prc, sum(vol) as vol "
         f"FROM moex.mt5_continuous WHERE ticker='{t}' AND bt>='2025-07-16' "
         "GROUP BY bt ORDER BY bt")
    df = ch.query_df(q)
    if df.empty: continue
    df['bt'] = pd.to_datetime(df['bt']).dt.tz_convert('Asia/Irkutsk')
    df = df[(df['bt'].dt.hour >= 15) | (df['bt'].dt.hour < 2)].copy()
    df['vol'] = df['vol'].clip(1).fillna(1)
    all_data[t] = df
    print(f'{t}: {len(df)} M1 bars', flush=True)
ch.close()

engine = PortfolioEngine([('dragon', dragon_check, list(all_data.keys()), None)],
    broker=BrokerSim(commission=4), capital=200000, slippage_in=1)
engine.executor.load_portfolio()

result = engine.run(all_data, ticker_specs=SPECS)
trades = result.trades; n = len(trades)
if n:
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins)/n*100
    pf = sum(wins)/sum(abs(p) for p in losses) if losses else 0
    cap = 200000; pk = cap; cash_mdd = 0
    for t in trades: cap += t.pnl; pk = max(pk, cap); cash_mdd = max(cash_mdd, (pk-cap)/pk*100)
    ret = (cap-200000)/200000*100
    mtm_mdd = getattr(result, 'mtm_max_dd', 0)
    print(f'\nResult: tr={n} wr={wr:.1f}% pf={pf:.2f} ret={ret:+.1f}% cashMDD={cash_mdd:.2f}% mtmMDD={mtm_mdd:.2f}%')
else:
    print('\n0 trades')
