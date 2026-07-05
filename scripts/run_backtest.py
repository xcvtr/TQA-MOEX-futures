#!/usr/bin/env python3
"""Run backtester once, save results to PG backtest.* tables.
Then visualize.py can read from PG in seconds."""
import sys, os, numpy as np, pandas as pd, psycopg2
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')

import strategies.common.backtester, strategies.common.engine, importlib
importlib.reload(strategies.common.backtester)
importlib.reload(strategies.common.engine)
from strategies.common.backtester import Backtester
from strategies.common.broker import BrokerSim
from strategies.common.engine import PortfolioEngine
from strategies.stop_hunt.prod.engine import check_signal as sh_check

CAPITAL = 200_000

print("Running backtest...", flush=True)
bt = Backtester(capital=CAPITAL, commission=4)
portfolio = bt.load_portfolio()
portfolio_sh = [(a, t, ['stop_hunt']) for a, t, s in portfolio]

data = bt.load_data(portfolio_sh)
if not data:
    print("No data"); sys.exit(1)

tickers = list(data.keys())
specs = bt.load_specs(tickers)

strategies = []
for asset, ticker, strats in portfolio_sh:
    if ticker not in data: continue
    for sname in strats:
        fn = {'stop_hunt': sh_check}.get(sname)
        if fn: strategies.append((sname, fn, [ticker], None))

broker = BrokerSim(commission=4)
engine = PortfolioEngine(strategies, broker=broker, capital=CAPITAL)
engine.executor.load_portfolio()
result = engine.run(data, specs)

balance = np.array(result.balance_curve)
mtm = np.array(result.mtm_curve)
trades = result.trades

# Get timestamps from the ticker with max bars
max_len = max(len(data[t]['bt']) for t in data)
ts_ticker = next(t for t in data if len(data[t]['bt']) >= max_len)
timestamps = pd.to_datetime(data[ts_ticker]['bt'].iloc[50:50+len(balance)].values)
if timestamps.tz is not None:
    timestamps = timestamps.tz_convert('Europe/Moscow')
else:
    timestamps = timestamps.tz_localize('Asia/Irkutsk').tz_convert('Europe/Moscow')
timestamps = timestamps.tz_localize(None)

# Stats
pnls = np.array([t.pnl for t in trades])
wins = pnls > 0
n = len(pnls)
wr = sum(wins)/n*100 if n > 0 else 0
pf = abs(sum(pnls[wins])/sum(pnls[~wins])) if sum(pnls[~wins]) != 0 and n > 0 else 0
peak = np.maximum.accumulate(balance)
dd = (peak - balance) / peak * 100
mdd = np.max(dd)
ret_pct = (balance[-1]/CAPITAL - 1)*100

# Save to PG
pg = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
cur = pg.cursor()

# Generate run_id (based on parameters)
run_id = f"sh_{CAPITAL//1000}k_{int(mdd*100)}_{int(balance[-1]/1000)}k"

# Clear previous same-run data
for tbl in ['backtest.equity_curve', 'backtest.trades']:
    cur.execute(f"DELETE FROM {tbl} WHERE run_id = %s", (run_id,))
cur.execute("DELETE FROM backtest.summary WHERE run_id = %s", (run_id,))

# Insert equity curve
eq_data = [(run_id, i, ts.isoformat(), float(balance[i]), float(mtm[i]), float(mtm[i]-balance[i]))
           for i, ts in enumerate(timestamps)]
cur.executemany(
    "INSERT INTO backtest.equity_curve VALUES (%s,%s,%s,%s,%s,%s)", eq_data)
print(f"  Saved {len(eq_data):,} equity curve points", flush=True)

# Insert trades
trade_data = []
for t in trades:
    entry_ts = timestamps[min(t.entry_bar, len(timestamps)-1)] if hasattr(t, 'entry_bar') else None
    # estimate exit time
    exit_ts = None
    trade_data.append((
        run_id, t.ticker, t.direction, t.strategy,
        t.entry_bar, float(t.entry_price),
        getattr(t, 'exit_bar', t.entry_bar + 12), float(t.exit_price or 0),
        float(t.pnl), t.exit_reason,
        entry_ts, exit_ts
    ))
cur.executemany(
    "INSERT INTO backtest.trades VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", trade_data)
print(f"  Saved {len(trade_data):,} trades", flush=True)

# Insert summary
cur.execute("""
    INSERT INTO backtest.summary (run_id, capital, final_equity, total_return_pct, mdd_pct, n_trades, win_rate, profit_factor)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
""", (run_id, CAPITAL, round(balance[-1], 2), round(ret_pct, 2), round(mdd, 2),
      n, round(wr, 1), round(pf, 3)))
pg.commit()
cur.close()
pg.close()

print(f"\n✅ Run ID: {run_id}")
print(f"   Equity: {CAPITAL:,} → {balance[-1]:,.0f}  (+{ret_pct:+.0f}%)")
print(f"   MDD: {mdd:.2f}%  |  Trades: {n}  |  WR: {wr:.1f}%  |  PF: {pf:.3f}")
print(f"\nTo draw chart: python3 scripts/visualize.py --run {run_id}")
