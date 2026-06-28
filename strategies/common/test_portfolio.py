"""
Portfolio backtest using the new engine→executor→broker architecture.
Tests all 4 strategies on the portfolio tickers.
"""
import clickhouse_connect as cc, numpy as np, psycopg2

from strategies.stop_hunt.prod.engine import check_signal as sh_check
from strategies.cvd.prod.engine import check_signal as cvd_check
from strategies.churn.prod.engine import check_signal as churn_check
from strategies.lunch_rev.prod.engine import check_signal as lunch_check
from strategies.common.executor import Executor

ch = cc.get_client(host='10.0.0.60', port=8123)
conn = psycopg2.connect(host='10.0.0.60', dbname='moex', user='user')
cur = conn.cursor()

PORTFOLIO = [
    ('GAZR', 'GZ', ['stop_hunt', 'cvd', 'churn']),
    ('SBRF', 'SR', ['stop_hunt', 'cvd', 'churn']),
    ('NG', 'NG', ['stop_hunt', 'churn']),
    ('VTBR', 'VB', ['stop_hunt', 'churn']),
    ('WHEAT', 'W4', ['stop_hunt', 'churn']),
]

# Load specs
specs = {}
for _, ticker, _ in PORTFOLIO:
    cur.execute("SELECT go, min_step, step_price, lot_volume FROM futures.ticker_specs WHERE ticker=%s", (ticker,))
    r = cur.fetchone()
    if r:
        specs[ticker] = {'go': float(r[0]), 'min_step': float(r[1]), 'step_price': float(r[2]), 'lot_volume': int(r[3])}

# Load data for all tickers
data = {}
for asset, ticker, _ in PORTFOLIO:
    df = ch.query_df(f"""
        SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
               argMax(pr_open,SYSTIME) as opn, argMax(pr_high,SYSTIME) as hi,
               argMax(pr_low,SYSTIME) as lo, argMax(pr_close,SYSTIME) as prc,
               sum(vol) as vol, sum(vol_b) as vb, sum(vol_s) as vs,
               argMax(oi_close,SYSTIME) as oi
        FROM moex.tradestats_fo
        WHERE asset_code='{asset}' AND SYSTIME >= '2024-10-01'
        GROUP BY bt ORDER BY bt
    """)
    if len(df) > 1000:
        data[ticker] = df

# Pre-compute signal data for each ticker
for ticker, df in data.items():
    n = len(df)
    prc = df['prc'].values.astype(float)
    hi = df['hi'].values.astype(float)
    lo = df['lo'].values.astype(float)
    vb = df['vb'].values.astype(float)
    vs = df['vs'].values.astype(float)
    vol = df['vol'].values.astype(float)
    oi = df['oi'].values.astype(float)
    
    # CVD z-score
    cvd_arr = vb - vs
    dcvd = np.diff(cvd_arr, prepend=cvd_arr[0])
    dcvd_z = np.zeros(n)
    for i in range(20, n):
        s = dcvd[i-20:i]
        if s.std() > 0: dcvd_z[i] = (dcvd[i] - s.mean()) / s.std()
    
    # Rolling windows for Churn
    vol_ma20 = np.zeros(n)
    sma20 = np.zeros(n)
    for i in range(20, n):
        vol_ma20[i] = np.mean(vol[i-20:i]) if np.mean(vol[i-20:i]) > 0 else 1
        sma20[i] = np.mean(prc[i-20:i])
    
    df['dcvd_z'] = dcvd_z
    df['vol_ma20'] = vol_ma20
    df['sma20'] = sma20

# Run portfolio
executor = Executor(initial_capital=100000, risk_pct=0.1)
max_len = max(len(df) for df in data.values())

for bar_idx in range(50, max_len):
    for asset, ticker, strategies in PORTFOLIO:
        df = data.get(ticker)
        if df is None or bar_idx >= len(df): continue
        
        row = df.iloc[bar_idx]
        s = specs.get(ticker, {})
        
        # Build bar_data for engines
        bar_data = {
            'prc': float(row['prc']), 'hi': float(row['hi']), 'lo': float(row['lo']),
            'opn': float(row['opn']), 'vol': float(row['vol']),
            'vb': float(row['vb']), 'vs': float(row['vs']), 'oi': float(row['oi']),
            'dcvd_z': float(row['dcvd_z']),
            'vol_ma20': float(row['vol_ma20']), 'sma20': float(row['sma20']),
            'oi_5ago': float(data[ticker]['oi'].iloc[max(0,bar_idx-5)]),
            'hour': row['bt'].hour if hasattr(row['bt'], 'hour') else 0,
            'minute': row['bt'].minute if hasattr(row['bt'], 'minute') else 0,
            'price_10': 0,
        }
        
        # Histories for Stop Hunt
        if bar_idx >= 20:
            bar_data['lo_hist'] = list(data[ticker]['lo'].iloc[bar_idx-20:bar_idx].values)
            bar_data['hi_hist'] = list(data[ticker]['hi'].iloc[bar_idx-20:bar_idx].values)
        else:
            bar_data['lo_hist'] = []
            bar_data['hi_hist'] = []
        
        # Check each assigned strategy
        for strat_name in strategies:
            signal = None
            if strat_name == 'stop_hunt':
                signal = sh_check(bar_data, ticker)
            elif strat_name == 'cvd':
                signal = cvd_check(bar_data, ticker)
            elif strat_name == 'churn':
                signal = churn_check(bar_data, ticker)
            
            if signal:
                executor.process_signal(signal, bar_idx, s)
    
    # Update all positions
    ticker_prices = {}
    for _, ticker, _ in PORTFOLIO:
        df = data.get(ticker)
        if df is not None and bar_idx < len(df):
            ticker_prices[ticker] = {
                'hi': float(df['hi'].iloc[bar_idx]),
                'lo': float(df['lo'].iloc[bar_idx]),
                'prc': float(df['prc'].iloc[bar_idx]),
            }
    
    for p in list(executor.positions):
        if not p.closed and p.ticker in ticker_prices:
            pr = ticker_prices[p.ticker]
            executor.broker.update(p, bar_idx, pr['hi'], pr['lo'], pr['prc'])
            if p.closed:
                executor.equity += p.pnl

# Results
ret = (executor.equity - 100000) / 100000 * 100
dd = executor.max_dd_pct
calmar = ret/dd if dd > 0 else 0

print(f"\n{'='*60}")
print(f"PORTFOLIO BACKTEST — NEW ARCHITECTURE")
print(f"{'='*60}")
print(f"Initial capital: 100,000 RUB")
print(f"Final equity:   {executor.equity:>10.0f} RUB")
print(f"Total return:   {ret:>+8.2f}%")
print(f"Max DD:         {dd:>6.2f}%")
print(f"Calmar:         {calmar:>7.2f}")
print(f"Total trades:   {len(executor.trades)}")
print(f"{'='*60}")

# Per-strategy breakdown
from collections import Counter
strat_stats = Counter()
for t in executor.trades:
    strat_stats[t.strategy] += 1
print(f"\nTrades per strategy:")
for s, n in sorted(strat_stats.items()):
    wins = sum(1 for t in executor.trades if t.strategy == s and t.pnl > 0)
    total = sum(t.pnl for t in executor.trades if t.strategy == s)
    print(f"  {s:15s}: {n:>4} trades, {wins:>4} wins, PnL={total:>+8.0f}")

cur.close(); conn.close()
