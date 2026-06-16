#!/usr/bin/env python3
"""
Verify 5 random trades against ClickHouse data.
FIXED: DB stores in Asia/Irkutsk tz, bare strings match in that tz.
"""
import json, re
from datetime import datetime, timedelta
import clickhouse_connect

ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

trades = [
    {'sym': 'AL', 'dir': 'L', 'entry_time': '2025-01-09 19:50:00+08:00', 'entry_price': 5700.0, 'exit_time': '2025-01-09 19:55:00+08:00', 'exit_price': 5710.0, 'pnl_rub': 43.85964912280702, 'reason': 'fade', 'contracts': 5, 'go': 5000.0},
    {'sym': 'SF', 'dir': 'S', 'entry_time': '2025-01-03 19:35:00+08:00', 'entry_price': 1426.4, 'exit_time': '2025-01-03 20:35:00+08:00', 'exit_price': 1422.6, 'pnl_rub': 39.960740325296356, 'reason': 'fade', 'contracts': 3, 'go': 5000.0},
    {'sym': 'Si', 'dir': 'L', 'entry_time': '2025-01-17 14:40:00+08:00', 'entry_price': 105450.0, 'exit_time': '2025-01-17 14:45:00+08:00', 'exit_price': 105306.71428571429, 'pnl_rub': -9.511616880037657, 'reason': 'stop', 'contracts': 7, 'go': 1000.0},
    {'sym': 'SN', 'dir': 'L', 'entry_time': '2025-01-16 13:35:00+08:00', 'entry_price': 25928.0, 'exit_time': '2025-01-16 14:25:00+08:00', 'exit_price': 26123.0, 'pnl_rub': 37.60413452638075, 'reason': 'fade', 'contracts': 1, 'go': 5000.0},
    {'sym': 'SR', 'dir': 'L', 'entry_time': '2025-01-15 13:50:00+08:00', 'entry_price': 28757.0, 'exit_time': '2025-01-15 19:15:00+08:00', 'exit_price': 29233.0, 'pnl_rub': 94.66369927322043, 'reason': 'fade', 'contracts': 1, 'go': 5719.0},
]

def get_bar(sym, ts_str):
    """Get bar from ClickHouse. DB stores in Asia/Irkutsk, so use bare time part."""
    # ts_str = '2025-01-09 19:50:00+08:00' -> '2025-01-09 19:50:00'
    bare_time = ts_str[:19]
    q = f"SELECT open,high,low,close,volume FROM moex.prices_5m WHERE symbol = '{sym}' AND time = '{bare_time}'"
    r = ch.query(q)
    if r.result_rows:
        row = r.result_rows[0]
        return {'open': float(row[0]), 'high': float(row[1]), 'low': float(row[2]), 'close': float(row[3]), 'volume': float(row[4])}
    return None

def get_bar_range(sym, start_ts, end_ts):
    """Get bars between timestamps (to verify stop hits)."""
    s = start_ts[:19]
    e = end_ts[:19]
    q = f"SELECT time, open, high, low, close, volume FROM moex.prices_5m WHERE symbol = '{sym}' AND time >= '{s}' AND time <= '{e}' ORDER BY time"
    r = ch.query(q)
    return [{'time': row[0], 'open': float(row[1]), 'high': float(row[2]), 'low': float(row[3]), 'close': float(row[4]), 'volume': float(row[5])} for row in r.result_rows]

errors = []
for i, t in enumerate(trades):
    print(f"\n{'='*70}")
    print(f"Trade {i+1}: {t['sym']} {t['dir']} | entry={t['entry_time']} -> exit={t['exit_time']} | reason={t['reason']}")
    print(f"{'='*70}")
    
    # 1. Check entry price
    entry_bar = get_bar(t['sym'], t['entry_time'])
    if entry_bar is None:
        print(f"  ⚠ ENTRY: No bar found at {t['entry_time'][:19]}")
        errors.append(f"T{i+1}: No entry bar for {t['sym']} at {t['entry_time']}")
    else:
        entry_price = t['entry_price']
        close_at_entry = entry_bar['close']
        diff_pct = abs(entry_price - close_at_entry) / close_at_entry * 100 if close_at_entry else 100
        match = diff_pct < 0.01
        print(f"  ENTRY: trade_price={entry_price:.2f}  db_close={close_at_entry:.2f}  diff={diff_pct:.4f}%  {'✓ MATCH' if match else '✗ MISMATCH'}")
        if not match:
            errors.append(f"T{i+1}: Entry price mismatch: trade={entry_price:.2f} db_close={close_at_entry:.2f} ({diff_pct:.4f}%)")
    
    # 2. Check exit price
    is_stop = t['reason'] == 'stop'
    exit_bar = get_bar(t['sym'], t['exit_time'])
    if exit_bar is None:
        print(f"  ⚠ EXIT: No bar found at {t['exit_time'][:19]}")
        errors.append(f"T{i+1}: No exit bar for {t['sym']} at {t['exit_time']}")
    else:
        exit_price = t['exit_price']
        close_at_exit = exit_bar['close']
        
        if is_stop:
            # Stop exit: exit_price = stop level, should be within bar's range
            low, high = exit_bar['low'], exit_bar['high']
            print(f"  EXIT: trade_price={exit_price:.2f} (STOP)  bar=[{low:.2f}..{high:.2f}]  close={close_at_exit:.2f}")
            in_range = low <= exit_price <= high
            print(f"    Stop in bar range? {'✓' if in_range else '✗'} (stop={exit_price:.2f}, range=[{low:.2f},{high:.2f}])")
            if not in_range:
                # Check bars between entry and exit for stop hit
                bars_range = get_bar_range(t['sym'], t['entry_time'], t['exit_time'])
                print(f"    Checking {len(bars_range)} bars from entry to exit:")
                hit = False
                for b in bars_range:
                    b_time_str = str(b['time'])
                    if t['dir'] == 'L' and b['low'] <= exit_price:
                        print(f"      ✓ Hit at {b_time_str}: low={b['low']:.2f} <= stop={exit_price:.2f}")
                        hit = True
                        break
                    elif t['dir'] == 'S' and b['high'] >= exit_price:
                        print(f"      ✓ Hit at {b_time_str}: high={b['high']:.2f} >= stop={exit_price:.2f}")
                        hit = True
                        break
                if not hit:
                    errors.append(f"T{i+1}: Stop not hit in any bar: sym={t['sym']} dir={t['dir']} stop={exit_price:.2f}")
        else:
            # Non-stop (fade/time/eod): exit_price should match bar close
            diff_pct = abs(exit_price - close_at_exit) / close_at_exit * 100 if close_at_exit else 100
            match = diff_pct < 0.01
            print(f"  EXIT: trade_price={exit_price:.2f}  db_close={close_at_exit:.2f}  diff={diff_pct:.4f}%  {'✓ MATCH' if match else '✗ MISMATCH'}")
            if not match:
                errors.append(f"T{i+1}: Exit price mismatch: trade={exit_price:.2f} db_close={close_at_exit:.2f} ({diff_pct:.4f}%)")
    
    # 3. Check PnL = direction * (exit-entry)/entry * go * contracts
    dm = 1 if t['dir'] == 'L' else -1
    expected_pnl = dm * (t['exit_price'] - t['entry_price']) / t['entry_price'] * t['go'] * t['contracts']
    actual_pnl = t['pnl_rub']
    pnl_diff = abs(expected_pnl - actual_pnl)
    pnl_match = pnl_diff < 0.1  # 10 kopecks tolerance
    print(f"  PnL:  actual={actual_pnl:.4f}  expected={expected_pnl:.4f}  diff={pnl_diff:.4f}  {'✓ MATCH' if pnl_match else '✗ MISMATCH'}")
    if not pnl_match:
        errors.append(f"T{i+1}: PnL mismatch: actual={actual_pnl:.4f} expected={expected_pnl:.4f}")

print(f"\n{'='*70}")
print(f"RESULTS SUMMARY")
print(f"{'='*70}")
if errors:
    print(f"FOUND {len(errors)} ISSUE(S):")
    for e in errors:
        print(f"  ✗ {e}")
else:
    print("  ✓ Все проверки пройдены — сделки корректны!")
