"""Quick smoke test of the new architecture — Stop Hunt on Si only."""
import clickhouse_connect as cc, numpy as np
from strategies.stop_hunt.prod.engine import check_signal
from strategies.common.executor import Executor

ch = cc.get_client(host='10.0.0.60', port=8123)

# Load Si data
df = ch.query_df("""
    SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
           argMax(pr_open,SYSTIME) as opn, argMax(pr_high,SYSTIME) as hi,
           argMax(pr_low,SYSTIME) as lo, argMax(pr_close,SYSTIME) as prc
    FROM moex.tradestats_fo
    WHERE asset_code='Si' AND SYSTIME >= '2025-01-01'
    GROUP BY bt ORDER BY bt
""")

print(f"Loaded {len(df)} bars for Si")

specs = {'go': 13284, 'min_step': 1.0, 'step_price': 1.0, 'lot_volume': 1000}
executor = Executor(initial_capital=100000, risk_pct=0.1)

for i in range(50, len(df)):
    row = df.iloc[i]
    bar_data = {
        'prc': float(row['prc']), 'hi': float(row['hi']), 'lo': float(row['lo']),
        'lo_hist': list(df['lo'].iloc[i-20:i].values),
        'hi_hist': list(df['hi'].iloc[i-20:i].values),
    }
    
    signal = check_signal(bar_data, 'Si')
    if signal:
        executor.process_signal(signal, i, specs)
    
    # Update positions
    for p in list(executor.positions):
        if not p.closed:
            hi = float(df['hi'].iloc[i])
            lo = float(df['lo'].iloc[i])
            prc = float(df['prc'].iloc[i])
            executor.broker.update(p, i, hi, lo, prc)
            if p.closed:
                executor.equity += p.pnl

ret = (executor.equity - 100000) / 100000 * 100
dd = executor.max_dd_pct
print(f"\n{'='*50}")
print(f"ARCHITECTURE SMOKE TEST — Stop Hunt Si")
print(f"{'='*50}")
print(f"Initial: 100,000 RUB")
print(f"Final:   {executor.equity:>8.0f} RUB")
print(f"Return:  {ret:>+7.2f}%")
print(f"MDD:     {dd:>5.2f}%")
print(f"Calmar:  {ret/dd:.2f}" if dd > 0 else "Calmar:  N/A")
print(f"Trades:  {len(executor.trades)}")
print(f"{'='*50}")
