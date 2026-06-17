#!/usr/bin/env python3
"""
OI-wave Strategy — Grid Search for TP/SL Multipliers.

Replaces the old exit logic:
- REMOVED: OI reverse exit
- REMOVED: 12h hold / ATR(14)×3 stop
- ADDED: TP = ATR(14)_entry × tp_mult, SL = ATR(14)_entry × sl_mult (FIXED at entry)
- ADDED: Time-stop = 48 hours (safety net)

Entry unchanged: |oi_z| > 2.0 for 3+ consecutive hours.

Grid: tp_mult × sl_mult over [1.0, 1.5, 2.0, 2.5, 3.0] = 25 combinations.
Optimization metric: Calmar ratio (return / max_dd).
Portfolio: 6 tickers (GK, AF, MG, YD, SR, NR), H1 bars, 0.01% slippage.
Kelly 20-50%, max 3 positions, period 2025-01-01 to 2026-06-01.
"""

import json
import os
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
TIME_STOP_HOURS = 48         # safety net
COOLDOWN_HOURS = 12
OI_Z_THRESHOLD = 2.0
MIN_WAVE_HOURS = 3
Z_WINDOW = 20

MAX_CONCURRENT_POSITIONS = 3
KELLY_INITIAL = 0.20
KELLY_MAX = 0.50
PER_TICKER_FRACTION = 0.15

REPORTS_DIR = '/home/user/projects/TQA-MOEX/reports/oi_wave_strategy'

# Grid search space
TP_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]
SL_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]


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

    # OI metrics
    dh['yur_total'] = dh['yur_buy'].fillna(0) + dh['yur_sell'].fillna(0)
    dh['fiz_total'] = dh['fiz_buy'].fillna(0) + dh['fiz_sell'].fillna(0)
    dh['oi_ratio'] = dh['yur_total'] / (dh['fiz_total'] + 1).clip(lower=1)

    # Z-score
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
    """Detect OI waves: |oi_z| > threshold for MIN_WAVE_HOURS consecutive hours."""
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

    if in_wave and wave_streak >= MIN_WAVE_HOURS:
        waves.append((wave_start, n - 1, wave_dir))

    return waves


def run_single_backtest(tp_mult, sl_mult, data, sym_times, all_waves, all_times):
    """
    Run a single backtest with given TP/SL multipliers.
    Returns summary metrics dict.
    """
    capital = INITIAL_CAPITAL
    equity_curve = []
    active_positions = {}
    cooldown_until = {}
    ticker_pnl = {sym: [] for sym in data}
    monthly_pnl = defaultdict(float)
    trade_count = 0
    win_count = 0
    total_fees = 0.0

    # Pre-compute wave start events
    wave_starts = defaultdict(list)
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
        # ── EXIT CHECKS ──
        to_close = []

        # 1) Check SL (fixed ATR at entry)
        for sym, pos in list(active_positions.items()):
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

        # 2) Check TP (fixed ATR at entry)
        for sym, pos in list(active_positions.items()):
            dh_test = sym_times[sym]
            if current_time in dh_test.index:
                bar = dh_test.loc[current_time]
                current_price = bar['close']
                if pos['direction'] == 1:  # LONG
                    if current_price >= pos['take_profit']:
                        if not any(s == sym for s, _, _, _ in to_close):
                            to_close.append((sym, current_time, current_price, 'take_profit'))
                else:  # SHORT
                    if current_price <= pos['take_profit']:
                        if not any(s == sym for s, _, _, _ in to_close):
                            to_close.append((sym, current_time, current_price, 'take_profit'))

        # 3) Time-stop (48h safety net)
        for sym, pos in list(active_positions.items()):
            time_stop = pos['entry_time'] + timedelta(hours=TIME_STOP_HOURS)
            if current_time >= time_stop:
                if not any(s == sym for s, _, _, _ in to_close):
                    dh_test = sym_times[sym]
                    if current_time in dh_test.index:
                        close_price = dh_test.loc[current_time, 'close']
                    else:
                        prev_idx = dh_test.index.get_indexer([current_time], method='ffill')[0]
                        close_price = dh_test.iloc[prev_idx]['close'] if prev_idx >= 0 else pos['entry_price']
                    to_close.append((sym, current_time, close_price, 'time_stop'))

        # ── Close positions ──
        for sym, close_time, close_price, reason in to_close:
            if sym not in active_positions:
                continue
            pos = active_positions[sym]
            exit_price = close_price * (1 + SLIPPAGE) if pos['direction'] == 1 else close_price * (1 - SLIPPAGE)
            pos['exit_time'] = close_time
            pos['exit_price'] = exit_price
            pos['exit_reason'] = reason

            if pos['direction'] == 1:
                pnl = pos['shares'] * (exit_price - pos['entry_price'])
            else:
                pnl = pos['shares'] * (pos['entry_price'] - exit_price)

            fees = pos['capital'] * SLIPPAGE * 2
            net_pnl = pnl - fees
            total_fees += fees
            capital += pos['capital'] + net_pnl

            trade_count += 1
            if net_pnl > 0:
                win_count += 1

            ticker_pnl[sym].append(net_pnl)
            monthly_key = close_time.strftime('%Y-%m')
            monthly_pnl[monthly_key] += net_pnl

            cooldown_until[sym] = close_time + timedelta(hours=COOLDOWN_HOURS)
            del active_positions[sym]

        # ── New entries ──
        if trade_count > 0:
            win_rate = win_count / trade_count
            kelly = max(KELLY_INITIAL, min(KELLY_MAX, win_rate - (1 - win_rate)))
        else:
            kelly = KELLY_INITIAL

        available_positions = MAX_CONCURRENT_POSITIONS - len(active_positions)

        if available_positions > 0:
            for sym in data:
                if sym in active_positions:
                    continue
                if sym in cooldown_until and current_time < cooldown_until[sym]:
                    continue
                if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
                    break

                dh_full = data[sym]['full']
                for ws in wave_starts[sym]:
                    wave_time = ws['time']
                    if wave_time == current_time or (abs((wave_time - current_time).total_seconds()) <= 3600
                                                      and wave_time <= current_time):
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

                        per_trade_capital = min(
                            capital * PER_TICKER_FRACTION,
                            capital * kelly / max(available_positions, 1)
                        )
                        per_trade_capital = min(per_trade_capital, capital * 0.5)

                        if per_trade_capital < 100:
                            continue

                        entry_price_with_slip = entry_price * (1 + SLIPPAGE) if direction == 1 else entry_price * (1 - SLIPPAGE)
                        shares = per_trade_capital / entry_price_with_slip

                        # NEW exit logic: fixed ATR at entry
                        if direction == 1:  # LONG
                            stop_loss = entry_price - atr_val * sl_mult
                            take_profit = entry_price + atr_val * tp_mult
                        else:  # SHORT
                            stop_loss = entry_price + atr_val * sl_mult
                            take_profit = entry_price - atr_val * tp_mult

                        active_positions[sym] = {
                            'direction': direction,
                            'entry_time': current_time,
                            'entry_price': entry_price_with_slip,
                            'stop_loss': stop_loss,
                            'take_profit': take_profit,
                            'shares': shares,
                            'capital': per_trade_capital,
                            'atr_entry': atr_val,
                        }
                        capital -= per_trade_capital
                        break

        # Equity
        current_equity = capital + sum(
            p['shares'] * sym_times[sym].loc[current_time, 'close']
            if current_time in sym_times[sym].index else p['entry_price']
            for sym, p in active_positions.items()
        )
        equity_curve.append({
            'time': str(current_time),
            'equity': round(current_equity, 2),
            'cash': round(capital, 2),
            'positions': len(active_positions),
        })

    # ── Close remaining positions ──
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
        capital += pos['capital'] + net_pnl

        trade_count += 1
        if net_pnl > 0:
            win_count += 1

    # ── Metrics ──
    final_capital = capital
    total_return = (final_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

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

    calmar = total_return / max_dd_pct if max_dd_pct > 0 else (float('inf') if total_return > 0 else 0)
    win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0
    test_days = (pd.Timestamp(END_TEST) - pd.Timestamp(START_TEST)).days
    profit_factor_val = 0.0
    gross_profit = sum(p for pnl_list in ticker_pnl.values() for p in pnl_list if p > 0)
    gross_loss = abs(sum(p for pnl_list in ticker_pnl.values() for p in pnl_list if p < 0))
    profit_factor_val = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0)

    return {
        'tp_mult': tp_mult,
        'sl_mult': sl_mult,
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
        'profit_factor': round(profit_factor_val, 2) if profit_factor_val != float('inf') else None,
        'trades_per_day': round(trade_count / max(test_days, 1), 3),
        'total_fees': round(total_fees, 2),
    }


def print_summary(results):
    """Print a formatted summary of one backtest result."""
    s = results
    print(f"  TP={s['tp_mult']:>4.1f} SL={s['sl_mult']:>4.1f} | "
          f"Return={s['total_return_pct']:>8.2f}% DD={s['max_drawdown_pct']:>6.2f}% "
          f"Calmar={s['calmar_ratio']:>7.2f} | "
          f"WinRate={s['win_rate_pct']:>5.1f}% PF={s['profit_factor'] or '∞':>5} "
          f"Trades={s['total_trades']:>3}")


def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    client = get_client()

    print("=" * 70)
    print("OI-Wave Strategy — Grid Search TP/SL Multipliers")
    print(f"Tickers: {', '.join(TICKERS)}")
    print(f"Period: {START_TEST} to {END_TEST}")
    print(f"tp_mult: {TP_MULTS}")
    print(f"sl_mult: {SL_MULTS}")
    print(f"Grid size: {len(TP_MULTS) * len(SL_MULTS)} combinations")
    print(f"Time-stop: {TIME_STOP_HOURS}h")
    print("=" * 70)

    # ── Load all ticker data (once) ──
    data = {}
    for sym in TICKERS:
        print(f"\nLoading {sym}...")
        dh = load_ticker_data(client, sym)
        if dh is not None:
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

    # ── Detect waves (once) ──
    all_waves = {}
    for sym, d in data.items():
        dh = d['full']
        waves = detect_waves(dh)
        all_waves[sym] = waves
        print(f"  {sym}: {len(waves)} waves detected")

    # ── Build timeline (once) ──
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

    print(f"\nTotal H1 bars in timeline: {len(all_times)}")
    print(f"\n{'=' * 70}")
    print("Running grid search...")
    print(f"{'=' * 70}")

    # ── Run all combinations ──
    all_results = []
    total_combos = len(TP_MULTS) * len(SL_MULTS)
    combo_idx = 0

    for tp_mult in TP_MULTS:
        for sl_mult in SL_MULTS:
            combo_idx += 1
            print(f"\n[{combo_idx}/{total_combos}] TP={tp_mult:.1f} SL={sl_mult:.1f} ...", end=" ")
            try:
                result = run_single_backtest(tp_mult, sl_mult, data, sym_times, all_waves, all_times)
                all_results.append(result)
                print_summary(result)
            except Exception as e:
                print(f"ERROR: {e}")
                all_results.append({
                    'tp_mult': tp_mult,
                    'sl_mult': sl_mult,
                    'error': str(e),
                    'calmar_ratio': -999,
                })

    # ── Sort by Calmar ratio ──
    all_results.sort(key=lambda r: r.get('calmar_ratio', -999), reverse=True)

    # ── Save all results ──
    output = {
        'strategy': 'OI-wave strategy — TP/SL Grid Search',
        'tickers': TICKERS,
        'test_period': {'start': START_TEST, 'end': END_TEST},
        'parameters': {
            'initial_capital': INITIAL_CAPITAL,
            'slippage_pct': SLIPPAGE * 100,
            'atr_period': ATR_PERIOD,
            'time_stop_hours': TIME_STOP_HOURS,
            'cooldown_hours': COOLDOWN_HOURS,
            'oi_z_threshold': OI_Z_THRESHOLD,
            'min_wave_hours': MIN_WAVE_HOURS,
            'z_window': Z_WINDOW,
            'max_concurrent_positions': MAX_CONCURRENT_POSITIONS,
            'kelly_initial': KELLY_INITIAL,
            'kelly_max': KELLY_MAX,
            'tp_mult_values': TP_MULTS,
            'sl_mult_values': SL_MULTS,
        },
        'results': all_results,
    }

    out_path = os.path.join(REPORTS_DIR, 'tp_sl_grid.json')
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n\nResults saved to {out_path}")

    # ── Print final table ──
    print("\n" + "=" * 70)
    print("GRID SEARCH RESULTS (sorted by Calmar)")
    print("=" * 70)
    print(f"{'#':<4} {'TP':<6} {'SL':<6} {'Return%':<10} {'DD%':<8} {'Calmar':<10} {'WinRate':<8} {'PF':<8} {'Trades':<8}")
    print(f"{'─'*4} {'─'*6} {'─'*6} {'─'*10} {'─'*8} {'─'*10} {'─'*8} {'─'*8} {'─'*8}")
    for i, r in enumerate(all_results):
        pf_str = f"{r.get('profit_factor', 0) or '∞':>6}"
        if r.get('error'):
            print(f"{i+1:<4} {r['tp_mult']:<6.1f} {r['sl_mult']:<6.1f} {'ERROR':<10} {'':8} {'':10} {'':8} {pf_str:<8} {'':8}")
        else:
            print(f"{i+1:<4} {r['tp_mult']:<6.1f} {r['sl_mult']:<6.1f} "
                  f"{r['total_return_pct']:<10.2f} {r['max_drawdown_pct']:<8.2f} "
                  f"{r['calmar_ratio']:<10.2f} {r['win_rate_pct']:<8.1f} "
                  f"{pf_str:<8} {r['total_trades']:<8}")

    # ── Top 5 detailed ──
    print("\n" + "=" * 70)
    print("TOP 5 COMBINATIONS — DETAILED")
    print("=" * 70)
    for i, r in enumerate(all_results[:5]):
        print(f"\n--- #{i+1}: TP={r['tp_mult']:.1f} × SL={r['sl_mult']:.1f} ---")
        print(f"  Final Capital:    {r['final_capital']:>12,.2f} RUB")
        print(f"  Total Return:     {r['total_return_pct']:>11.2f}%")
        print(f"  Total PnL:        {r['total_pnl']:>12,.2f} RUB")
        print(f"  Max Drawdown:     {r['max_drawdown_pct']:>10.2f}% ({r['max_drawdown_rub']:,.0f} RUB)")
        print(f"  Calmar Ratio:     {r['calmar_ratio']:>11.2f}")
        print(f"  Win Rate:         {r['win_rate_pct']:>10.1f}%")
        print(f"  Profit Factor:    {r.get('profit_factor', 'N/A'):>10}")
        print(f"  Total Trades:     {r['total_trades']:>11}")
        print(f"  Trades/Day:       {r['trades_per_day']:>10.3f}")

    return output


if __name__ == '__main__':
    main()
