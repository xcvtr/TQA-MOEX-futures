"""Smoke test — executor + broker, проверка архитектуры."""
import clickhouse_connect as cc
from strategies.stop_hunt.prod.engine import check_signal
from strategies.common.executor import Executor

ch = cc.get_client(host='10.0.0.60', port=8123)

df = ch.query_df("""
    SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
           argMax(pr_high,SYSTIME) as hi, argMax(pr_low,SYSTIME) as lo,
           argMax(pr_close,SYSTIME) as prc
    FROM moex.tradestats_fo WHERE asset_code='Si' AND SYSTIME >= '2024-10-01'
    GROUP BY bt ORDER BY bt
""")

specs = {'go': 13284, 'step_price': 1.0, 'min_step': 1.0, 'lot_volume': 1000}
executor = Executor(initial_capital=100000)

for i in range(50, len(df)):
    row = df.iloc[i]
    signal = check_signal({
        'prc': float(row['prc']), 'hi': float(row['hi']), 'lo': float(row['lo']),
        'lo_hist': list(df['lo'].iloc[i-20:i].values),
        'hi_hist': list(df['hi'].iloc[i-20:i].values),
    }, 'Si')
    
    if signal:
        executor.process_signal(signal, i, specs)
    
    executor.update_positions(i, float(df['hi'].iloc[i]),
                                 float(df['lo'].iloc[i]),
                                 float(df['prc'].iloc[i]))

ret = executor.total_return_pct
dd = executor.max_dd_pct
print(f"Capital: {executor.initial:>6.0f} → {executor.equity:>8.0f}")
print(f"Return:  {ret:>+7.2f}%")
print(f"MDD:     {dd:>5.2f}%")
print(f"Calmar:  {ret/dd:.2f}" if dd > 0 else "N/A")
print(f"Trades:  {len(executor.trades)}")

from collections import Counter
reasons = Counter(t.exit_reason for t in executor.trades)
print(f"Exits:   {dict(reasons)}")
