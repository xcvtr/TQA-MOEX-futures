#!/usr/bin/env python3
"""
OI-wave Strategy Backtest for top-6 tickers (GK, AF, MG, YD, SR, NR).

Uses ClickHouse data from moex.prices_5m + moex.prices_5m_oi.
Resamples to H1, computes OI z-score (window=20), detects waves (|oi_z|>2.0 for 3+ hours).
Trades on wave start: LONG if oi_z>0, SHORT if oi_z<0.
Hold 12h, stop ATR(14)×3, no re-entry during wave+12h.
Kelly-adaptive sizing (20-50%), max 3 concurrent positions.
Portfolio: equal distribution, start capital 100,000 RUB.

Test period: 2025-01-01 to 2026-06-01 (OOS).
"""

import json
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import clickhouse_connect

warnings.filterwarnings('ignore')

# ── Config ──────────────────────────────────────────────────────────────
TICKERS = ['GK', 'AF', 'MG', 'YD', 'SR', 'NR']
START_DATA = '2024-01-01'
END_DATA = '2026-06-01'
START_TEST = '2025-01-01'
END_TEST = '2026-06-01'

INITIAL_CAPITAL = 100_000.0
SLIPPAGE = 0.0001           # 0.01%
ATR_PERIOD = 14
ATR_MULT = 3.0
HOLD_HOURS = 12
COOLDOWN_HOURS = 12
OI_Z_THRESHOLD = 2.0
MIN_WAVE_HOURS = 3
Z_WINDOW = 20

MAX_CONCURRENT_POSITIONS = 3
KELLY_INITIAL = 0.20
KELLY_MAX = 0.50
PER_TICKER_FRACTION = 0.15  # max allocation per ticker as fraction of capital

REPORTS_DIR = '/home/user/projects/TQA-MOEX/reports/oi_wave_strategy'

# ── Helpers ─────────────────────────────────────────────────────────────

def get_client():
    return clickhouse_connect.get_client(host='localhost', port=8123)


def load_ticker_data(client, symbol):
    """Load 5m prices + OI for a ticker and resample to H1."""
    q = f"""
    SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
           o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
    FROM moex.prices_5m p
    INNER JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
    WHERE p.symbol = '{symbol}'
      AND p.time >= '{START_DATA}'
      AND p.time < '{END_DATA}'
    ORDER BY p.time
    """
    rows = client.query(q).result_rows
    if not rows or len(rows) < 500:
        print(f"  {symbol}: insufficient data ({len(rows) if rows else 0} rows)")
        return None

    cols = ['time', 'open', 'high', 'low', 'close', 'volume',
            'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell', 'total_oi']
    df = pd.DataFrame(rows, columns=cols)
    if df['time'].dt.tz is not None:
        df['time'] = df['time'].dt.tz_localize(None)
    df.set_index('time', inplace=True)

    # Resample to H1
    agg = {
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
        'volume': 'sum',
        'fiz_buy': 'last', 'fiz_sell': 'last',
        'yur_buy': 'last', 'yur_sell': 'last', 'total_oi': 'last'
    }
    dh = df.resample('1h').agg(agg).dropna(subset=['close'])
    if len(dh) < 100:
        print(f"  {symbol}: too few H1 bars ({len(dh)})")
        return None

    # Calculate OI metrics
    dh['yur_total'] = dh['yur_buy'].fillna(0) + dh['yur_sell'].fillna(0)
    dh['fiz_total'] = dh['fiz_buy'].fillna(0) + dh['fiz_sell'].fillna(0)
    dh['oi_ratio'] = dh['yur_total'] / (dh['fiz_total'] + 1).clip(lower=1)

    # Z-score with rolling window
    dh['oi_ratio_mean'] = dh['oi_ratio'].rolling(Z_WINDOW, min_periods=Z_WINDOW).mean()
    dh['oi_ratio_std'] = dh['oi_ratio'].rolling(Z_WINDOW, min_periods=Z_WINDOW).std()
    dh['oi_z'] = (dh['oi_ratio'] - dh['oi_ratio_mean']) / dh['oi_ratio_std'].clip(lower=1e-10)

    # ATR
    dh['tr'] = np.maximum(
        dh['high'] - dh['low'],
        np.maximum(
            abs(dh['high'] - dh['close'].shift(1)),
            abs(dh['low'] - dh['close'].shift(1))
        )
    )
    dh['atr'] = dh['tr'].rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

    return dh


def detect_waves(dh):
    """Detect OI waves: |oi_z| > threshold for MIN_WAVE_HOURS consecutive hours.
    Returns list of waves: [(start_idx, end_idx, direction), ...]
    direction: 1 for LONG (oi_z > 0), -1 for SHORT (oi_z < 0)
    """
    waves = []
    in_wave = False
    wave_start = None
    wave_dir = None
    wave_streak = 0
    n = len(dh)

    for i in range(n):
        oi_z = dh['oi_z'].iloc[i]
        if pd.isna(oi_z):
            if in_wave:
                # End wave if we hit NaN
                if wave_streak >= MIN_WAVE_HOURS:
                    waves.append((wave_start, i - 1, wave_dir))
                in_wave = False
                wave_start = None
                wave_dir = None
                wave_streak = 0
            continue

        is_active = abs(oi_z) > OI_Z_THRESHOLD
        cur_dir = 1 if oi_z > 0 else -1

        if is_active:
            if not in_wave:
                in_wave = True
                wave_start = i
                wave_dir = cur_dir
                wave_streak = 1
            elif cur_dir == wave_dir:
                wave_streak += 1
            else:
                # Direction changed - end current wave, start new
                if wave_streak >= MIN_WAVE_HOURS:
                    waves.append((wave_start, i - 1, wave_dir))
                in_wave = True
                wave_start = i
                wave_dir = cur_dir
                wave_streak = 1
        else:
            if in_wave:
                if wave_streak >= MIN_WAVE_HOURS:
                    waves.append((wave_start, i - 1, wave_dir))
                in_wave = False
                wave_start = None
                wave_dir = None
                wave_streak = 0

    # Catch trailing wave
    if in_wave and wave_streak >= MIN_WAVE_HOURS:
        waves.append((wave_start, n - 1, wave_dir))

    return waves


def run_backtest():
    """Run the OI-wave backtest for all tickers."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    client = get_client()

    print("=" * 70)
    print("OI-Wave Strategy Backtest")
    print(f"Tickers: {', '.join(TICKERS)}")
    print(f"Period: {START_TEST} to {END_TEST}")
    print(f"Initial Capital: {INITIAL_CAPITAL:,.0f} RUB")
    print(f"Slippage: {SLIPPAGE*100:.3f}%")
    print(f"ATR Stop: {ATR_MULT}× ATR(14)")
    print(f"Hold: {HOLD_HOURS}h, Cooldown: {COOLDOWN_HOURS}h")
    print(f"Kelly: {KELLY_INITIAL*100:.0f}% → {KELLY_MAX*100:.0f}%")
    print(f"Max positions: {MAX_CONCURRENT_POSITIONS}")
    print("=" * 70)

    # ── Load all ticker data ──
    data = {}
    for sym in TICKERS:
        print(f"\nLoading {sym}...")
        dh = load_ticker_data(client, sym)
        if dh is not None:
            # Filter to test period
            mask = (dh.index >= START_TEST) & (dh.index < END_TEST)
            dh_test = dh[mask].copy()
            if len(dh_test) < 50:
                print(f"  {sym}: insufficient test data ({len(dh_test)} bars)")
                continue
            data[sym] = {'full': dh, 'test': dh_test}
            print(f"  {sym}: {len(dh_test)} H1 bars in test period")

    if not data:
        print("No data available. Exiting.")
        return

    # ── Detect waves for each ticker ──
    all_waves = {}
    for sym, d in data.items():
        dh = d['full']
        waves = detect_waves(dh)
        all_waves[sym] = waves
        print(f"  {sym}: {len(waves)} waves detected")

    # ── Simulate trading ──
    # Build unified timeline of all H1 bars in test period
    all_times = set()
    sym_times = {}
    for sym, d in data.items():
        times = set(d['test'].index)
        sym_times[sym] = d['test']
        all_times.update(times)
    all_times = sorted(all_times)

    if not all_times:
        print("No timeline bars. Exiting.")
        return

    # State tracking
    capital = INITIAL_CAPITAL
    equity_curve = []
    active_positions = {}  # sym -> position dict
    cooldown_until = {}    # sym -> datetime (no re-entry until)
    ticker_pnl = {sym: [] for sym in data}
    monthly_pnl = defaultdict(float)
    trade_log = []
    trade_count = 0
    win_count = 0
    total_fees = 0.0

    # Pre-compute wave start events within test period
    wave_starts = defaultdict(list)  # sym -> [(time, direction, wave_end_idx)]
    for sym in data:
        dh_full = data[sym]['full']
        for start_idx, end_idx, direction in all_waves.get(sym, []):
            wave_start_time = dh_full.index[start_idx]
            if wave_start_time < pd.Timestamp(START_TEST):
                continue
            wave_starts[sym].append({
                'time': wave_start_time,
                'direction': direction,
                'start_idx': start_idx,
                'end_idx': end_idx,
            })

    for t_idx, current_time in enumerate(all_times):
        # Update ATR stops for open positions
        to_close = []
        for sym, pos in list(active_positions.items()):
            # Check stop loss
            dh_test = sym_times[sym]
            if current_time in dh_test.index:
                bar = dh_test.loc[current_time]
                current_price = bar['close']
                if pos['direction'] == 1:  # LONG
                    if current_price <= pos['stop_loss']:
                        to_close.append((sym, current_time, current_price, 'stop_loss'))
                else:  # SHORT
                    if current_price >= pos['stop_loss']:
                        to_close.append((sym, current_time, current_price, 'stop_loss'))

        # Close positions that hit stop
        for sym, close_time, close_price, reason in to_close:
            if sym not in active_positions:
                continue
            pos = active_positions[sym]
            exit_price = close_price * (1 + SLIPPAGE) if pos['direction'] == 1 else close_price * (1 - SLIPPAGE)
            pos['exit_time'] = close_time
            pos['exit_price'] = exit_price
            pos['exit_reason'] = reason

            # PnL
            if pos['direction'] == 1:
                pnl = pos['shares'] * (exit_price - pos['entry_price'])
            else:
                pnl = pos['shares'] * (pos['entry_price'] - exit_price)

            fees = pos['capital'] * SLIPPAGE * 2  # entry + exit
            net_pnl = pnl - fees
            total_fees += fees
            capital += pos['capital'] + net_pnl

            pos['pnl'] = pnl
            pos['net_pnl'] = net_pnl
            pos['return_pct'] = (net_pnl / pos['capital']) * 100

            trade_count += 1
            if net_pnl > 0:
                win_count += 1

            ticker_pnl[sym].append(net_pnl)
            monthly_key = close_time.strftime('%Y-%m')
            monthly_pnl[monthly_key] += net_pnl

            # Cooldown
            cooldown_until[sym] = close_time + timedelta(hours=COOLDOWN_HOURS)

            entry_str = close_time.strftime('%Y-%m-%d %H:%M') if isinstance(close_time, pd.Timestamp) else str(close_time)
            trade_log.append({
                'ticker': sym,
                'direction': 'LONG' if pos['direction'] == 1 else 'SHORT',
                'entry_time': pos['entry_time'].strftime('%Y-%m-%d %H:%M') if hasattr(pos['entry_time'], 'strftime') else str(pos['entry_time']),
                'exit_time': entry_str,
                'entry_price': round(pos['entry_price'], 2),
                'exit_price': round(exit_price, 2),
                'shares': round(pos['shares'], 4),
                'capital_used': round(pos['capital'], 2),
                'pnl': round(pnl, 2),
                'net_pnl': round(net_pnl, 2),
                'return_pct': round(pos['return_pct'], 2),
                'exit_reason': reason,
            })
            del active_positions[sym]

        # Check for time-based exits (hold period expired)
        to_close_time = []
        for sym, pos in list(active_positions.items()):
            hold_end = pos['entry_time'] + timedelta(hours=HOLD_HOURS)
            if current_time >= hold_end:
                to_close_time.append((sym, current_time))

        for sym, close_time in to_close_time:
            if sym not in active_positions:
                continue
            pos = active_positions[sym]
            dh_test = sym_times[sym]
            if current_time in dh_test.index:
                close_price = dh_test.loc[current_time, 'close']
            else:
                # Use last available price
                prev_idx = dh_test.index.get_indexer([current_time], method='ffill')[0]
                if prev_idx >= 0:
                    close_price = dh_test.iloc[prev_idx]['close']
                else:
                    close_price = pos['entry_price']

            exit_price = close_price * (1 + SLIPPAGE) if pos['direction'] == 1 else close_price * (1 - SLIPPAGE)
            pos['exit_time'] = close_time
            pos['exit_price'] = exit_price
            pos['exit_reason'] = 'time_exit'

            if pos['direction'] == 1:
                pnl = pos['shares'] * (exit_price - pos['entry_price'])
            else:
                pnl = pos['shares'] * (pos['entry_price'] - exit_price)

            fees = pos['capital'] * SLIPPAGE * 2
            net_pnl = pnl - fees
            total_fees += fees
            capital += pos['capital'] + net_pnl

            pos['pnl'] = pnl
            pos['net_pnl'] = net_pnl
            pos['return_pct'] = (net_pnl / pos['capital']) * 100

            trade_count += 1
            if net_pnl > 0:
                win_count += 1

            ticker_pnl[sym].append(net_pnl)
            monthly_key = close_time.strftime('%Y-%m')
            monthly_pnl[monthly_key] += net_pnl

            cooldown_until[sym] = close_time + timedelta(hours=COOLDOWN_HOURS)

            trade_log.append({
                'ticker': sym,
                'direction': 'LONG' if pos['direction'] == 1 else 'SHORT',
                'entry_time': pos['entry_time'].strftime('%Y-%m-%d %H:%M') if hasattr(pos['entry_time'], 'strftime') else str(pos['entry_time']),
                'exit_time': close_time.strftime('%Y-%m-%d %H:%M') if isinstance(close_time, pd.Timestamp) else str(close_time),
                'entry_price': round(pos['entry_price'], 2),
                'exit_price': round(exit_price, 2),
                'shares': round(pos['shares'], 4),
                'capital_used': round(pos['capital'], 2),
                'pnl': round(pnl, 2),
                'net_pnl': round(net_pnl, 2),
                'return_pct': round(pos['return_pct'], 2),
                'exit_reason': 'time_exit',
            })
            del active_positions[sym]

        # ── Check for new entries ──
        # Kelly-adaptive sizing
        if trade_count > 0:
            win_rate = win_count / trade_count
            kelly = max(KELLY_INITIAL, min(KELLY_MAX, win_rate - (1 - win_rate)))  # simple kelly
        else:
            kelly = KELLY_INITIAL

        available_positions = MAX_CONCURRENT_POSITIONS - len(active_positions)

        if available_positions > 0:
            # Find which tickers have waves starting at this bar
            for sym in data:
                if sym in active_positions:
                    continue
                if sym in cooldown_until and current_time < cooldown_until[sym]:
                    continue
                if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
                    break

                # Check if wave just started at this time (within ±1 hour)
                dh_full = data[sym]['full']
                for ws in wave_starts[sym]:
                    wave_time = ws['time']
                    if wave_time == current_time or (abs((wave_time - current_time).total_seconds()) <= 3600
                                                      and wave_time <= current_time):
                        # Found a wave start — enter position
                        direction = ws['direction']
                        dh_test = sym_times[sym]
                        if current_time in dh_test.index:
                            bar = dh_test.loc[current_time]
                        else:
                            continue

                        entry_price = bar['close']
                        atr_val = bar['atr']
                        if pd.isna(entry_price) or entry_price == 0 or pd.isna(atr_val):
                            continue

                        # Position sizing
                        per_trade_capital = min(
                            capital * PER_TICKER_FRACTION,
                            capital * kelly / max(available_positions, 1)
                        )
                        per_trade_capital = min(per_trade_capital, capital * 0.5)  # never use >50%

                        if per_trade_capital < 100:  # minimum trade
                            continue

                        entry_price_with_slip = entry_price * (1 + SLIPPAGE) if direction == 1 else entry_price * (1 - SLIPPAGE)
                        shares = per_trade_capital / entry_price_with_slip

                        stop_price = entry_price - atr_val * ATR_MULT if direction == 1 else entry_price + atr_val * ATR_MULT

                        active_positions[sym] = {
                            'direction': direction,
                            'entry_time': current_time,
                            'entry_price': entry_price_with_slip,
                            'stop_loss': stop_price,
                            'shares': shares,
                            'capital': per_trade_capital,
                            'atr_entry': atr_val,
                        }
                        capital -= per_trade_capital
                        break  # only one entry per ticker

        # Record equity
        current_equity = capital + sum(p['shares'] * sym_times[sym].loc[current_time, 'close']
                                       if current_time in sym_times[sym].index else p['entry_price']
                                       for sym, p in active_positions.items())
        equity_curve.append({
            'time': str(current_time),
            'equity': round(current_equity, 2),
            'cash': round(capital, 2),
            'positions': len(active_positions),
        })

    # ── Close remaining positions at end of test ──
    for sym, pos in list(active_positions.items()):
        dh_test = sym_times[sym]
        close_time = all_times[-1]
        if close_time in dh_test.index:
            close_price = dh_test.loc[close_time, 'close']
        else:
            close_price = pos['entry_price']

        exit_price = close_price * (1 + SLIPPAGE) if pos['direction'] == 1 else close_price * (1 - SLIPPAGE)

        if pos['direction'] == 1:
            pnl = pos['shares'] * (exit_price - pos['entry_price'])
        else:
            pnl = pos['shares'] * (pos['entry_price'] - exit_price)

        fees = pos['capital'] * SLIPPAGE * 2
        net_pnl = pnl - fees
        total_fees += fees
        capital += pos['capital'] + net_pnl

        pos['pnl'] = pnl
        pos['net_pnl'] = net_pnl
        pos['exit_time'] = close_time
        pos['exit_price'] = exit_price
        pos['exit_reason'] = 'end_of_test'

        trade_count += 1
        if net_pnl > 0:
            win_count += 1
        ticker_pnl[sym].append(net_pnl)
        monthly_key = close_time.strftime('%Y-%m')
        monthly_pnl[monthly_key] += net_pnl

        trade_log.append({
            'ticker': sym,
            'direction': 'LONG' if pos['direction'] == 1 else 'SHORT',
            'entry_time': pos['entry_time'].strftime('%Y-%m-%d %H:%M') if hasattr(pos['entry_time'], 'strftime') else str(pos['entry_time']),
            'exit_time': 'END',
            'entry_price': round(pos['entry_price'], 2),
            'exit_price': round(exit_price, 2),
            'shares': round(pos['shares'], 4),
            'capital_used': round(pos['capital'], 2),
            'pnl': round(pnl, 2),
            'net_pnl': round(net_pnl, 2),
            'return_pct': round((net_pnl / pos['capital']) * 100, 2),
            'exit_reason': 'end_of_test',
        })

    # ── Compute metrics ──
    final_capital = capital
    total_return = (final_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    # Drawdown
    equity_values = [e['equity'] for e in equity_curve]
    peak = equity_values[0]
    max_dd = 0
    max_dd_pct = 0
    for eq in equity_values:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    # Calmar
    calmar = total_return / max_dd_pct if max_dd_pct > 0 else float('inf')

    # Trades stats
    win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0
    test_days = (pd.Timestamp(END_TEST) - pd.Timestamp(START_TEST)).days
    trades_per_day = trade_count / max(test_days, 1)

    # Per-ticker PnL
    ticker_summary = {}
    for sym in data:
        pnls = ticker_pnl.get(sym, [])
        if pnls:
            total_pnl = sum(pnls)
            n = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            ticker_summary[sym] = {
                'trades': n,
                'total_pnl': round(total_pnl, 2),
                'avg_pnl': round(total_pnl / n, 2),
                'wins': wins,
                'losses': n - wins,
                'win_rate': round(wins / n * 100, 1),
            }

    # Monthly PnL
    monthly_summary = {}
    for month in sorted(monthly_pnl.keys()):
        monthly_summary[month] = round(monthly_pnl[month], 2)

    # ── Build results ──
    results = {
        'strategy': 'OI-wave strategy',
        'tickers': TICKERS,
        'test_period': {'start': START_TEST, 'end': END_TEST},
        'parameters': {
            'initial_capital': INITIAL_CAPITAL,
            'slippage_pct': SLIPPAGE * 100,
            'atr_multiplier': ATR_MULT,
            'atr_period': ATR_PERIOD,
            'hold_hours': HOLD_HOURS,
            'cooldown_hours': COOLDOWN_HOURS,
            'oi_z_threshold': OI_Z_THRESHOLD,
            'min_wave_hours': MIN_WAVE_HOURS,
            'z_window': Z_WINDOW,
            'max_concurrent_positions': MAX_CONCURRENT_POSITIONS,
            'kelly_initial': KELLY_INITIAL,
            'kelly_max': KELLY_MAX,
        },
        'summary': {
            'final_capital': round(final_capital, 2),
            'total_return_pct': round(total_return, 2),
            'total_pnl': round(final_capital - INITIAL_CAPITAL, 2),
            'max_drawdown_rub': round(max_dd, 2),
            'max_drawdown_pct': round(max_dd_pct, 2),
            'calmar_ratio': round(calmar, 2),
            'total_trades': trade_count,
            'win_count': win_count,
            'loss_count': trade_count - win_count,
            'win_rate_pct': round(win_rate, 1),
            'trades_per_day': round(trades_per_day, 3),
            'total_fees': round(total_fees, 2),
        },
        'per_ticker': ticker_summary,
        'monthly_pnl': monthly_summary,
        'trades': trade_log,
    }

    # Save
    out_path = os.path.join(REPORTS_DIR, 'backtest.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved to {out_path}")

    # ── Print summary ──
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    s = results['summary']
    print(f"\n📊 Overall Performance:")
    print(f"  Final Capital:    {s['final_capital']:>12,.2f} RUB")
    print(f"  Total Return:     {s['total_return_pct']:>11.2f}%")
    print(f"  Total PnL:        {s['total_pnl']:>12,.2f} RUB")
    print(f"  Max Drawdown:     {s['max_drawdown_pct']:>10.2f}% ({s['max_drawdown_rub']:,.0f} RUB)")
    print(f"  Calmar Ratio:     {s['calmar_ratio']:>11.2f}")

    print(f"\n📈 Trade Statistics:")
    print(f"  Total Trades:     {s['total_trades']:>11}")
    print(f"  Win Rate:         {s['win_rate_pct']:>10.1f}%")
    print(f"  Trades/Day:       {s['trades_per_day']:>10.3f}")

    print(f"\n📋 Per-Ticker PnL:")
    print(f"  {'Ticker':<6} {'Trades':<8} {'Total PnL':<12} {'Avg PnL':<10} {'WinRate':<8}")
    print(f"  {'-'*44}")
    for sym, ts in sorted(ticker_summary.items()):
        print(f"  {sym:<6} {ts['trades']:<8} {ts['total_pnl']:<12,.2f} {ts['avg_pnl']:<10,.2f} {ts['win_rate']:<8}%")

    print(f"\n📅 Monthly PnL:")
    print(f"  {'Month':<8} {'PnL':<12}")
    print(f"  {'-'*20}")
    for month, pnl in monthly_summary.items():
        sign = '+' if pnl >= 0 else ''
        print(f"  {month:<8} {sign}{pnl:<11,.2f}")

    return results


if __name__ == '__main__':
    results = run_backtest()
