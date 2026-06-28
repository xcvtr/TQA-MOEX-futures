"""Fixed smoke test — debug the executor flow."""
import clickhouse_connect as cc, numpy as np
from strategies.stop_hunt.prod.engine import check_signal
from strategies.common.broker import Position
from strategies.common.executor import Executor

ch = cc.get_client(host='10.0.0.60', port=8123)

df = ch.query_df("""
    SELECT toStartOfInterval(SYSTIME,INTERVAL 5 MINUTE) as bt,
           argMax(pr_high,SYSTIME) as hi,
           argMax(pr_low,SYSTIME) as lo,
           argMax(pr_close,SYSTIME) as prc
    FROM moex.tradestats_fo
    WHERE asset_code='Si' AND SYSTIME >= '2024-10-01'
    GROUP BY bt ORDER BY bt
""")

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
        pos = executor.process_signal(signal, i, specs)
        if pos:
            pass  # position opened
    
    # Update ALL open positions (iterate a COPY)
    for p in list(executor.positions):
        if p.closed:
            continue
        hi = float(df['hi'].iloc[i])
        lo = float(df['lo'].iloc[i])
        prc = float(df['prc'].iloc[i])
        
        # Manual trailing TP + timeout check (bypass broker for now)
        entry = p.entry_price
        if p.direction == 'long':
            fav = (hi - entry) / entry * 100
            cur_dn = (entry - lo) / entry * 100
        else:
            fav = (entry - lo) / entry * 100
            cur_dn = (hi - entry) / entry * 100
        
        if fav > p.best_price: p.best_price = fav
        if not p.trail_activated and fav >= 0.5: p.trail_activated = True
        
        closed = False
        if p.trail_activated:
            trail_stop = p.best_price - 0.3
            if cur_dn >= trail_stop:
                exit_px = entry * (1 + trail_stop/100) if p.direction == 'long' else entry * (1 - trail_stop/100)
                ticks = (exit_px - entry) / p.min_step * (1 if p.direction == 'long' else -1)
                pnl = ticks * p.step_price * p.shares - 4 * p.shares
                p.pnl = round(pnl, 2)
                p.exit_reason = 'trailing_tp'
                p.closed = True
                executor.equity += pnl
                executor.trades.append(p)
                closed = True
        
        if not closed and i - p.entry_bar >= 12:
            exit_px = prc
            ticks = (exit_px - entry) / p.min_step * (1 if p.direction == 'long' else -1)
            pnl = ticks * p.step_price * p.shares - 4 * p.shares
            p.pnl = round(pnl, 2)
            p.exit_reason = 'timeout'
            p.closed = True
            executor.equity += pnl
            executor.trades.append(p)

# Remove closed from positions
executor.positions = [p for p in executor.positions if not p.closed]

ret = (executor.equity - 100000) / 100000 * 100

# Compute proper DD
peak = 100000.0; max_dd = 0.0
eq = 100000.0
pnl_by_bar = {}
for t in executor.trades:
    pnl_by_bar.setdefault(t.entry_bar, 0)
    if t.exit_reason != 'open':
        pass  # PnL was already added to equity

# Actually track real equity through trades
eq = 100000.0; peak = 100000.0; max_dd = 0.0
for t in sorted(executor.trades, key=lambda x: x.entry_bar):
    eq += t.pnl
    if eq > peak: peak = eq
    dd = (peak - eq) / peak * 100
    if dd > max_dd: max_dd = dd

print(f"\n{'='*50}")
print(f"ARCHITECTURE TEST — FIXED")
print(f"{'='*50}")
print(f"Final equity: {executor.equity:>8.0f}")
print(f"Return:       {ret:>+7.2f}%")
print(f"MDD:          {max_dd:>5.2f}%")
print(f"Calmar:       {ret/max_dd:.2f}" if max_dd > 0 else "N/A")
print(f"Trades:       {len(executor.trades)}")

# Per exit reason
from collections import Counter
reasons = Counter(t.exit_reason for t in executor.trades)
print(f"\nExit reasons: {dict(reasons)}")
