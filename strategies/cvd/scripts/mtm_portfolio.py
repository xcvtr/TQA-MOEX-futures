#!/usr/bin/env python3
"""
Bar-by-bar MTM portfolio simulation: CVD divergence + YURz (OI) filter.
Tickers: NG, BR, Si (MOEX futures).
Walk-forward per ticker, bar-by-bar MTM loop, capital splitting on concurrent signals.

Usage:
    python scripts/mtm_portfolio_cvd_yur.py
    python scripts/mtm_portfolio_cvd_yur.py --no-yur-filter --capital 500000
    python scripts/mtm_portfolio_cvd_yur.py --yur-filter --date-from 2025-06-01 --output results/eq.csv
"""

import os, sys, argparse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import clickhouse_connect
import psycopg2
import warnings
warnings.filterwarnings('ignore', '.*to_period.*', UserWarning)

# ── Path setup ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))

from lib_cvd_divergence import (
    TICK, TICK_COST, GO, SYMBOLS,
    LK, HOLD_BARS, Q, INITIAL_CAPITAL,
    SLIPPAGE_IN_TICKS, SLIPPAGE_OUT_TICKS,
    calc_thresholds, detect_signals,
    walk_forward_split, resample_to_5m,
)
from config import CH_HOST, CH_PORT, CH_DB

# ── PG config for ticker specs ──
DB_HOST = os.environ.get('MOEX_DB_HOST', '10.0.0.64')
DB_PORT = int(os.environ.get('MOEX_DB_PORT', 5432))
DB_NAME = os.environ.get('MOEX_DB_NAME', 'moex')
DB_USER = os.environ.get('MOEX_DB_USER', 'postgres')
DB_PASS = os.environ.get('MOEX_DB_PASSWORD', 'postgres')


def load_ticker_specs_from_pg(tickers):
    """Load real LOT, initial_margin, min_step, step_price from PG moex_ticker_specs.

    Returns {ticker: {'lot': int, 'initial_margin': float, 'min_step': float,
                      'step_price': float}}
    On any error (no data, connect fail) falls back to old hardcoded values.
    """
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT,
            dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            connect_timeout=5,
        )
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(tickers))
        cur.execute(f'''
            SELECT DISTINCT ON (asset_code)
                asset_code, lot_volume, initial_margin, min_step, step_price
            FROM moex_ticker_specs
            WHERE asset_code IN ({placeholders})
            ORDER BY asset_code, trade_date DESC
        ''', list(tickers))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            raise ValueError(f'No specs found for {tickers} in PG')
        specs = {}
        for r in rows:
            specs[r[0]] = {
                'lot': int(r[1]),
                'initial_margin': float(r[2]),
                'min_step': float(r[3]),
                'step_price': float(r[4]),
            }
        missing = [t for t in tickers if t not in specs]
        if missing:
            print(f'  ⚠ Missing PG specs for: {missing}, using hardcoded fallback')
        return specs
    except Exception as e:
        print(f'  ⚠ PG load_ticker_specs failed ({e}), using hardcoded fallback')
        # Hardcoded fallback — correct values from ISS as of 2026-06-27
        return {
            'NG': {'lot': 100, 'initial_margin': 6760.87, 'min_step': 0.001, 'step_price': 7.56347},
            'BR': {'lot': 10,  'initial_margin': 11672.59, 'min_step': 0.01, 'step_price': 7.56347},
            'Si': {'lot': 1000, 'initial_margin': 12098.90, 'min_step': 1.0, 'step_price': 1.0},
        }


# ── OI filter config (from paper trader) ──
OI_FILTER_CONFIG = {
    'NG': {'tf_min': 240, 'yur_z': 2.0},
    'BR': {'tf_min': 15,  'yur_z': 2.0},
    'Si': {'tf_min': 60,  'yur_z': 1.0},
}
TICKERS = ['NG', 'BR', 'Si']
TICKER_TO_OI = {'NG': 'NG', 'BR': 'BR', 'Si': 'Si'}

# ────

# ─────────────────────────────────────────────
#  1. DATA LOADING
# ─────────────────────────────────────────────


def load_cvd_data(ch, ticker, date_from, date_to):
    """Load CVD data from moex.tradestats_fo and resample to 5m."""
    df = ch.query_df(f"""
        SELECT toDateTime(tradedate || ' ' || tradetime) AS time,
               tradedate AS date,
               pr_open AS open, pr_high AS high, pr_low AS low,
               pr_close AS close, vol_b, vol_s
        FROM moex.tradestats_fo
        WHERE asset_code = '{ticker}' AND vol > 0
          AND tradedate >= '{date_from}' AND tradedate <= '{date_to}'
        ORDER BY time
    """)
    if not df.empty:
        df['time'] = df['time'].dt.tz_localize(None)
    if df.empty:
        return pd.DataFrame()
    return resample_to_5m(df)


def load_oi_data(ticker, ch, date_from, date_to):
    """Load OI data from moex.prices_5m_oi, compute yur_net_z at configured tf."""
    oi_sym = TICKER_TO_OI.get(ticker)
    if oi_sym is None:
        return None
    cfg = OI_FILTER_CONFIG.get(ticker)
    if cfg is None:
        return None
    tf_min = cfg['tf_min']
    q = f"""
        SELECT time, yur_buy, yur_sell FROM moex.prices_5m_oi
        WHERE symbol='{oi_sym}' AND time >= '{date_from}' AND time <= '{date_to}'
        ORDER BY time
    """
    try:
        rows = ch.query(q).result_rows
    except Exception:
        return None
    if not rows or len(rows) < 20:
        return None
    df = pd.DataFrame(rows, columns=['time', 'yur_buy', 'yur_sell'])
    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
    for c in ['yur_buy', 'yur_sell']:
        df[c] = df[c].astype(float)
    df['yur_net'] = df['yur_buy'] - df['yur_sell']

    df = df.set_index('time')
    r = df['yur_net'].resample(f'{tf_min}min', closed='right', label='right').last().dropna()
    r = r.to_frame('yur_net')
    r['yur_net_chg'] = r['yur_net'].diff()

    window = max(10, 20 * 5 // max(tf_min, 5))
    m = r['yur_net_chg'].rolling(window, min_periods=5).mean()
    s = r['yur_net_chg'].rolling(window, min_periods=5).std().clip(lower=1e-10)
    r['yur_net_z'] = (r['yur_net_chg'] - m) / s
    return r.reset_index()[['time', 'yur_net_z', 'yur_net_chg']]


def check_yur_filter(ticker, direction, signal_time, oi_cache):
    """Check YURz filter: confuence direction * yur_net_chg + |z| >= threshold."""
    cfg = OI_FILTER_CONFIG.get(ticker)
    if cfg is None or oi_cache is None or oi_cache.empty:
        return True
    mask = oi_cache['time'] <= signal_time
    if not mask.any():
        return True
    nearest = oi_cache[mask].iloc[-1]
    if pd.isna(nearest['yur_net_z']) or pd.isna(nearest['yur_net_chg']):
        return True
    if direction * nearest['yur_net_chg'] <= 0:
        return False
    if abs(nearest['yur_net_z']) < cfg['yur_z']:
        return False
    return True


# ─────────────────────────────────────────────
#  2. SIGNAL DETECTION
# ─────────────────────────────────────────────


def detect_cvd_signals_with_exit(df, p_thr, c_thr, lk=LK, hold_bars=HOLD_BARS):
    """Detect CVD divergence signals, return list of dicts with entry/exit info.

    Args:
        df: 5m DataFrame with 'signal' column (output of detect_signals)
        p_thr, c_thr: thresholds (not used directly, detect_signals does the work)
        lk: lookback
        hold_bars: number of bars to hold (exit = close of bar at index i+hold_bars)

    Returns:
        List of dicts: {time, direction, side, entry_price, exit_time, exit_price,
                        entry_bar_idx, exit_bar_idx, cvd_signal}
    """
    df_signals = detect_signals(df, p_thr, c_thr, lk)
    if df_signals.empty:
        return []

    signals = []
    for i in range(len(df_signals)):
        sig = int(df_signals.iloc[i]['signal'])
        if sig == 0:
            continue

        entry_time = df_signals.iloc[i]['time']
        entry_price = float(df_signals.iloc[i]['close'])

        exit_idx = min(i + hold_bars, len(df_signals) - 1)
        exit_time = df_signals.iloc[exit_idx]['time']
        exit_price = float(df_signals.iloc[exit_idx]['close'])

        signals.append({
            'time': entry_time,
            'direction': sig,           # 1=LONG, -1=SHORT
            'side': 'LONG' if sig == 1 else 'SHORT',
            'entry_price': entry_price,
            'exit_time': exit_time,
            'exit_price': exit_price,
            'entry_bar_idx': i,
            'exit_bar_idx': exit_idx,
            'cvd_signal': 'TROUGH→LONG' if sig == 1 else 'CREST→SHORT',
        })
    return signals


# ─────────────────────────────────────────────
#  3. WALK-FORWARD PER TICKER → COLLECT SIGNALS
# ─────────────────────────────────────────────


def collect_signals(data, tickers, lk, q, hold_bars, date_from):
    """Run walk-forward for each ticker, collect all signals.

    Returns list of signal dicts with 'ticker' key added.
    """
    all_signals = []
    for ticker in tickers:
        if ticker not in data:
            continue
        df = data[ticker]
        if df.empty or len(df) < 200:
            print(f"  {ticker}: insufficient data ({len(df)} bars) — skipping")
            continue

        dates = sorted(df['date'].unique())
        ws_train = min(180, max(60, len(dates) // 3))
        ws_test = min(60, max(20, len(dates) // 6))

        ticker_count = 0
        for train_dates, test_dates in walk_forward_split(dates, ws_train, ws_test):
            train = df[df['date'].isin(train_dates)].copy()
            test = df[df['date'].isin(test_dates)].copy().reset_index(drop=True)

            if len(train) < 50 or len(test) < 10:
                continue

            p_thr, c_thr = calc_thresholds(train, lk, q)
            if p_thr is None:
                continue

            sigs = detect_cvd_signals_with_exit(test, p_thr, c_thr, lk, hold_bars)
            for s in sigs:
                s['ticker'] = ticker
                all_signals.append(s)
                ticker_count += 1

        print(f"  {ticker}: {ticker_count} signals collected")

    # Sort by time
    all_signals.sort(key=lambda x: x['time'])
    # Filter to test period — ensure tz-naive comparison
    date_from_ts = pd.Timestamp(date_from)
    if hasattr(date_from_ts, 'tz') and date_from_ts.tz is not None:
        date_from_ts = date_from_ts.tz_localize(None)
    filtered = []
    for s in all_signals:
        st = s['time']
        if hasattr(st, 'tz') and st.tz is not None:
            st = st.tz_localize(None)
            s['time'] = st
        if st >= date_from_ts:
            filtered.append(s)
    all_signals = filtered
    print(f"  Total: {len(all_signals)} signals after date filter")
    return all_signals


# ─────────────────────────────────────────────
#  4. MTM PORTFOLIO LOOP
# ─────────────────────────────────────────────


def calc_atr_series(df, period=20):
    """Calculate ATR series for 5m bars. Returns Series with atr values indexed by time."""
    if df.empty or len(df) < period + 2:
        return pd.Series(dtype=float)
    df = df.copy()
    prev_close = df['close'].shift(1)
    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs(),
        )
    )
    atr = tr.ewm(span=period, adjust=False).mean()
    return pd.Series(atr.values, index=df['time'])


def run_mtm_portfolio(data, oi_data, signals, tickers,
                       initial_capital, use_yur_filter,
                       hold_bars, lk=20, q=0.6):
    """
    Bar-by-bar MTM portfolio simulation with RISK-BASED position sizing.

    Sizing = risk / (ATR × lot), capped at max_leverage × equity.
    DD-aware: risk halves at 10% DD, stops at 20% DD.

    For each bar:
      1. Close positions whose exit_time <= bar_time -> return capital + PnL to free_cash
      2. Check for NEW signals at this bar_time
      3. Risk-based sizing with ATR, leverage cap, and DD control
      4. Record equity = free_cash + sum(floating PnL of open positions)
    """
    # Load real ticker specs from PG (fallback hardcoded if PG unavailable)
    specs = load_ticker_specs_from_pg(tickers)

    def lot(tkr):
        return specs.get(tkr, {}).get('lot', 1)

    def initial_margin(tkr):
        return specs.get(tkr, {}).get('initial_margin', 10000.0)

    def tick_min_step(tkr):
        return specs.get(tkr, {}).get('min_step', 0.001)

    def tick_step_price(tkr):
        return specs.get(tkr, {}).get('step_price', 1.0)

    # ── Risk-based sizing config ──
    POSITION_RISK = 0.008      # 0.8% of equity per trade
    MAX_LEVERAGE = 10          # max 10x notional/equity
    DD_RISK_HALVE = 0.10       # halve risk at 10% DD
    DD_STOP = 0.20             # stop trading at 20% DD

    # ── Pre-compute ATR for each ticker ──
    print("  Pre-computing ATR...")
    atr_data = {}
    for tkr in tickers:
        df = data.get(tkr)
        if df is not None and not df.empty:
            atr_data[tkr] = calc_atr_series(df, period=20)
            print(f"    {tkr}: ATR available ({len(atr_data[tkr])} bars)")

    # Build lookup: (ticker, time) -> signal
    signals_by_key = {}
    for tkr in tickers:
        for s in signals.get(tkr, []):
            signals_by_key[(tkr, s['time'])] = s

    # All unique bar timestamps
    all_times = set()
    for tkr in tickers:
        df = data.get(tkr)
        if df is not None and not df.empty:
            for t in df['time']:
                all_times.add(pd.Timestamp(t).tz_localize(None))
    all_times = sorted(all_times)

    if not all_times:
        return pd.DataFrame(), [], {}

    free_cash = float(initial_capital)
    open_positions = {}
    trades = []
    equity_records = []
    peak_equity = float(initial_capital)  # for DD tracking

    def get_price_at(tkr, bar_time):
        df = data.get(tkr)
        if df is None or df.empty:
            return None
        bar_time_naive = pd.Timestamp(bar_time).tz_localize(None)
        mask = df['time'] <= bar_time_naive
        if not mask.any():
            return None
        return float(df[mask].iloc[-1]['close'])

    for bar_time in all_times:
        # 1. Close positions with exit_time <= bar_time
        to_close = []
        for tkr, pos in open_positions.items():
            exit_t = pd.Timestamp(pos['exit_time']).tz_localize(None)
            if exit_t <= bar_time:
                exit_price = get_price_at(tkr, bar_time) or pos['entry_price']
                ms = tick_min_step(tkr)
                sp = tick_step_price(tkr)
                mult = sp / ms
                tick_cost = TICK_COST.get(tkr, 1.0)
                slippage_rub = (SLIPPAGE_IN_TICKS + SLIPPAGE_OUT_TICKS) * tick_cost * pos['lots']
                pnl_rub = (exit_price - pos['entry_price']) * pos['direction'] * pos['lots'] * mult - slippage_rub
                free_cash += pos['allocated'] + pnl_rub
                trades.append({
                    'close_time': bar_time, 'ticker': tkr,
                    'side': pos['side'], 'entry_time': pos['entry_time'],
                    'entry_price': round(pos['entry_price'], 6),
                    'exit_price': round(exit_price, 6),
                    'lots': round(pos['lots'], 4),
                    'allocated': round(pos['allocated'], 2),
                    'pnl_rub': round(pnl_rub, 2),
                    'hold_bars': hold_bars,
                    'signal_type': pos['signal_type'],
                })
                to_close.append(tkr)
        for tkr in to_close:
            del open_positions[tkr]

        # 2. Find new signals at this bar_time
        new_signals = []
        for tkr in tickers:
            if tkr in open_positions:
                continue
            sig = signals_by_key.get((tkr, bar_time))
            if sig is not None:
                new_signals.append((tkr, sig))

        # 3. Open new positions
        # 3a. Calculate current floating PnL of existing positions (for equity calc before new allocation)
        cur_floating = 0.0
        for tkr, pos in open_positions.items():
            cur_price = get_price_at(tkr, bar_time) or pos['entry_price']
            ms = tick_min_step(tkr)
            sp = tick_step_price(tkr)
            mult = sp / ms * lot(tkr)
            cur_floating += (cur_price - pos['entry_price']) * pos['direction'] * pos['lots'] * mult
        current_equity = free_cash + cur_floating

        # 3b. Open new positions with RISK-BASED sizing
        #     lots = risk_amount / (ATR × lot)  — capped at max_leverage × equity
        if new_signals and current_equity > 0:
            # Compute current DD for risk control
            peak_equity = max(peak_equity, current_equity)
            current_dd = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0

            # Apply DD-aware risk reduction
            effective_risk = POSITION_RISK
            if current_dd >= DD_STOP:
                effective_risk = 0.0  # stop trading
            elif current_dd >= DD_RISK_HALVE:
                effective_risk = POSITION_RISK * 0.5

            risk_per_trade = current_equity * effective_risk

            for tkr, sig in new_signals:
                if risk_per_trade <= 0:
                    break
                entry_price = float(sig['entry_price'])
                direction = int(sig.get('direction', 1))
                direction_sign = 1 if direction > 0 else -1

                # ATR-based sizing: lots = risk_amount / (atr_val * lot)
                atr_series = atr_data.get(tkr)
                if atr_series is not None and not atr_series.empty:
                    atr_mask = atr_series.index <= bar_time
                    if atr_mask.any():
                        atr_val = float(atr_series[atr_mask].iloc[-1])
                    else:
                        atr_val = entry_price * 0.001
                else:
                    atr_val = entry_price * 0.001
                atr_val = max(atr_val, tick_min_step(tkr))  # floor at 1 tick

                risk_per_lot = atr_val * lot(tkr)  # RUB per lot per ATR move
                lots = risk_per_trade / max(risk_per_lot, 1.0)

                # Leverage cap: notional <= MAX_LEVERAGE × equity
                notional_per_lot = entry_price * lot(tkr)
                max_lots_leverage = (current_equity * MAX_LEVERAGE) / max(notional_per_lot, 1.0)
                lots = min(lots, max_lots_leverage)

                # Margin check
                margin = initial_margin(tkr)
                if margin <= 0:
                    continue
                margin_needed = lots * margin
                if margin_needed > free_cash:
                    lots = max(0, free_cash / margin)
                    margin_needed = lots * margin

                lots = max(lots, 0)
                if lots < 0.01:
                    continue  # too small to trade

                actual_alloc = lots * margin
                free_cash -= actual_alloc
                exit_time = sig.get('exit_time', bar_time + timedelta(hours=1))
                open_positions[tkr] = {
                    'entry_time': bar_time, 'entry_price': entry_price,
                    'exit_time': exit_time, 'direction': direction_sign,
                    'side': 'LONG' if direction_sign > 0 else 'SHORT',
                    'lots': round(lots, 4), 'allocated': round(actual_alloc, 2),
                    'signal_type': sig.get('cvd_signal', 'unknown'),
                }

        # 4. Floating PnL (including new positions) and equity
        floating_pnl = 0.0
        for tkr, pos in open_positions.items():
            cur_price = get_price_at(tkr, bar_time) or pos['entry_price']
            ms = tick_min_step(tkr)
            sp = tick_step_price(tkr)
            mult = sp / ms * lot(tkr)
            floating_pnl += (cur_price - pos['entry_price']) * pos['direction'] * pos['lots'] * mult

        equity_records.append({
            'time': bar_time,
            'equity': round(free_cash + floating_pnl, 2),
            'free_cash': round(free_cash, 2),
            'positions_open': len(open_positions),
            'floating_pnl': round(floating_pnl, 2),
        })

    # 5. Force-close remaining at end
    if open_positions and all_times:
        last_time = all_times[-1]
        for tkr, pos in list(open_positions.items()):
            exit_price = get_price_at(tkr, last_time) or pos['entry_price']
            ms = tick_min_step(tkr)
            sp = tick_step_price(tkr)
            mult = sp / ms * lot(tkr)
            tick_cost = TICK_COST.get(tkr, 1.0)
            slippage_rub = (SLIPPAGE_IN_TICKS + SLIPPAGE_OUT_TICKS) * tick_cost * pos['lots']
            pnl_rub = (exit_price - pos['entry_price']) * pos['direction'] * pos['lots'] * mult - slippage_rub
            trades.append({
                'close_time': last_time, 'ticker': tkr,
                'side': pos['side'], 'entry_time': pos['entry_time'],
                'entry_price': round(pos['entry_price'], 6),
                'exit_price': round(exit_price, 6),
                'lots': round(pos['lots'], 4),
                'allocated': round(pos['allocated'], 2),
                'pnl_rub': round(pnl_rub, 2),
                'hold_bars': hold_bars,
                'signal_type': pos['signal_type'],
            })
        open_positions.clear()

    equity_df = pd.DataFrame(equity_records)
    final_equity = equity_df['equity'].iloc[-1] if not equity_df.empty else initial_capital
    metrics = compute_metrics(equity_df, trades, final_equity, initial_capital)
    return equity_df, trades, metrics



def compute_metrics(equity_df, trades, final_capital, initial_capital):
    """Compute performance metrics from equity curve and trades list."""
    if equity_df.empty or len(equity_df) < 2:
        return {
            'total_return_pct': 0, 'cagr_pct': 0, 'max_dd_pct': 0,
            'sharpe': 0, 'calmar': 0, 'duration_days': 0,
            'final_capital': round(final_capital, 2),
            'total_trades': 0, 'win_rate': 0, 'avg_hold_bars': 0,
        }

    total_return = (final_capital / initial_capital - 1) * 100

    start_date = equity_df['time'].min()
    end_date = equity_df['time'].max()
    duration_days = (end_date - start_date).days
    duration_years = max(duration_days / 365.25, 0.1)

    ratio = final_capital / initial_capital
    cagr = (ratio ** (1 / duration_years) - 1) if ratio > 0 else -1.0

    # Max drawdown
    eq_arr = equity_df['equity'].values
    peak_arr = np.maximum.accumulate(eq_arr)
    dd_arr = (peak_arr - eq_arr) / peak_arr * 100
    max_dd = float(np.max(dd_arr)) if len(dd_arr) > 0 else 0

    # Daily Sharpe
    eq_daily = equity_df.copy()
    eq_daily['date'] = eq_daily['time'].dt.date
    daily = eq_daily.groupby('date').last().reset_index()
    daily['ret'] = daily['equity'].pct_change().fillna(0)
    sharpe = np.sqrt(252) * daily['ret'].mean() / max(daily['ret'].std(), 1e-10) if len(daily) > 5 else 0

    calmar = (cagr * 100) / max(max_dd, 0.01)

    # Trade stats
    n_trades = len(trades)
    win_rate = (sum(1 for t in trades if t['pnl_rub'] > 0) / max(n_trades, 1)) * 100
    avg_hold = np.mean([t['hold_bars'] for t in trades]) if trades else 0

    return {
        'total_return_pct': round(total_return, 2),
        'cagr_pct': round(cagr * 100, 2),
        'max_dd_pct': round(max_dd, 2),
        'sharpe': round(float(sharpe), 3),
        'calmar': round(float(calmar), 3),
        'duration_days': duration_days,
        'final_capital': round(final_capital, 2),
        'total_trades': n_trades,
        'win_rate': round(win_rate, 2),
        'avg_hold_bars': round(float(avg_hold), 1),
    }


# ─────────────────────────────────────────────
#  5. CLI + MAIN
# ─────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description='MTM Portfolio: CVD divergence + YURz OI filter (NG, BR, Si)'
    )
    parser.add_argument('--yur-filter', action='store_true', default=True,
                        dest='yur_filter', help='Enable YURz OI filter (default)')
    parser.add_argument('--no-yur-filter', action='store_false',
                        dest='yur_filter', help='Disable YURz OI filter')
    parser.add_argument('--capital', type=float, default=200000.0,
                        help='Initial capital in RUB (default: 200000)')
    parser.add_argument('--date-from', default='2025-01-01',
                        help='Test start date (default: 2025-01-01)')
    parser.add_argument('--date-to', default='2026-06-27',
                        help='Test end date (default: 2026-06-27)')
    parser.add_argument('--warmup-from', default='2024-01-01',
                        help='Warmup/backfill start date (default: 2024-01-01)')
    parser.add_argument('--output', default='equity_curve.csv',
                        help='Output CSV path for equity curve')
    parser.add_argument('--lk', type=int, default=LK,
                        help=f'Lookback period (default: {LK})')
    parser.add_argument('--q', type=float, default=Q,
                        help=f'Quantile threshold (default: {Q})')
    parser.add_argument('--hold-bars', type=int, default=HOLD_BARS,
                        help=f'Hold bars (default: {HOLD_BARS})')
    args = parser.parse_args()

    filter_label = 'YURz ON' if args.yur_filter else 'YURz OFF'
    print(f"{'='*70}")
    print(f"  MTM Portfolio: CVD Divergence + {filter_label}")
    print(f"{'='*70}")
    print(f"  Capital:   {args.capital:>10,.0f} RUB")
    print(f"  Period:    {args.date_from}  →  {args.date_to}")
    print(f"  Warmup:    {args.warmup_from}")
    print(f"  Params:    lk={args.lk}, q={args.q}, hold={args.hold_bars}")
    print(f"{'='*70}")

    # ── Connect ──
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    print(f"\n✓ Connected to ClickHouse at {CH_HOST}:{CH_PORT}/{CH_DB}")

    # ── Load CVD data ──
    print("\n── Loading CVD data ──")
    data = {}
    for t in TICKERS:
        print(f"  {t}: loading from {args.warmup_from} to {args.date_to}...", end=' ')
        sys.stdout.flush()
        df = load_cvd_data(ch, t, args.warmup_from, args.date_to)
        if df.empty:
            print("⚠ NO DATA")
            continue
        print(f"✓ {len(df)} bars ({df.iloc[0]['time'].strftime('%Y-%m-%d')} .. "
              f"{df.iloc[-1]['time'].strftime('%Y-%m-%d')})")
        data[t] = df

    # ── Load OI data ──
    print("\n── Loading OI data ──")
    oi_data = {}
    for t in TICKERS:
        print(f"  {t}: loading OI...", end=' ')
        sys.stdout.flush()
        oi = load_oi_data(t, ch, args.warmup_from, args.date_to)
        if oi is not None:
            print(f"✓ {len(oi)} OI bars")
        else:
            print("⚠ N/A (filter disabled)")
        oi_data[t] = oi

    if not data:
        print("\n✗ No data for any ticker. Aborting.")
        return

    # ── Collect signals (walk-forward) ──
    print("\n── Collecting signals (walk-forward) ──")
    signals = collect_signals(data, TICKERS, args.lk, args.q,
                              args.hold_bars, args.date_from)
    if not signals:
        print("  No signals found.")
        if args.yur_filter:
            print("  Try --no-yur-filter to see if OI filter is too strict.")
        return

    # ── Group signals by ticker ──
    signals_by_ticker: dict[str, list[dict]] = {}
    for s in signals:
        tkr = s.get('ticker', '')
        if tkr:
            signals_by_ticker.setdefault(tkr, []).append(s)

    print(f"  Signals by ticker: " + ", ".join(
        f"{tkr}={len(v)}" for tkr, v in sorted(signals_by_ticker.items())
    ))

    # ── Run both modes ──
    results = {}
    for use_filter, label in [(True, 'with YURz'), (False, 'without YURz')]:
        print(f"\n── Running MTM portfolio ({label}) ──")
        eq_df, trds, met = run_mtm_portfolio(
            data, oi_data, signals_by_ticker, TICKERS,
            args.capital, use_filter, args.hold_bars,
        )
        results[label] = (eq_df, trds, met)
        print(f"  Trades: {met.get('total_trades', 0)} | "
              f"Final: {met.get('final_capital', 0):,.0f} RUB | "
              f"Return: {met.get('total_return_pct', 0):+.2f}%")
        print(f"  CAGR: {met.get('cagr_pct', 0):+.2f}% | "
              f"MaxDD: {met.get('max_dd_pct', 0):.2f}% | "
              f"Sharpe: {met.get('sharpe', 0):.3f} | "
              f"Calmar: {met.get('calmar', 0):.3f}")
        print(f"  WinRate: {met.get('win_rate', 0):.1f}% | "
              f"AvgHold: {met.get('avg_hold_bars', 0):.1f} bars")

    # ── Print comparison table ──
    print(f"\n{'='*70}")
    print(f"  {'COMPARISON':^66}")
    print(f"{'='*70}")
    header = f"{'Metric':<25} {'With YURz':>18} {'Without YURz':>18}"
    print(header)
    print('-' * 70)
    met_keys = ['total_return_pct', 'cagr_pct', 'max_dd_pct', 'sharpe', 'calmar',
                'total_trades', 'win_rate', 'avg_hold_bars', 'final_capital']
    for key in met_keys:
        v1 = results.get('with YURz', ({}, {}, {}))[2].get(key, 'N/A')
        v2 = results.get('without YURz', ({}, {}, {}))[2].get(key, 'N/A')
        if isinstance(v1, float):
            print(f"  {key:<25} {v1:>18.4f} {v2:>18.4f}")
        else:
            print(f"  {key:<25} {str(v1):>18} {str(v2):>18}")
    print('-' * 70)

    # ── Save equity curve CSV ──
    eq_with, _, _ = results.get('with YURz', (pd.DataFrame(), [], {}))
    eq_without, _, _ = results.get('without YURz', (pd.DataFrame(), [], {}))

    # Rename columns to differentiate
    if not eq_with.empty:
        eq_w = eq_with.rename(columns={
            'equity': 'equity_with_yurz',
            'free_cash': 'free_cash_with_yurz',
            'positions_open': 'positions_open_with_yurz',
            'floating_pnl': 'floating_pnl_with_yurz',
        })
    else:
        eq_w = pd.DataFrame({'time': []})

    if not eq_without.empty:
        eq_wo = eq_without.rename(columns={
            'equity': 'equity_without_yurz',
            'free_cash': 'free_cash_without_yurz',
            'positions_open': 'positions_open_without_yurz',
            'floating_pnl': 'floating_pnl_without_yurz',
        })
    else:
        eq_wo = pd.DataFrame({'time': []})

    # Merge
    if not eq_w.empty and not eq_wo.empty:
        eq_merged = pd.merge(eq_w, eq_wo, on='time', how='outer').sort_values('time')
    elif not eq_w.empty:
        eq_merged = eq_w
    else:
        eq_merged = eq_wo

    if not eq_merged.empty:
        # Resolve output path
        output_path = args.output
        if not os.path.isabs(output_path):
            output_path = os.path.join(SCRIPT_DIR, output_path)
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        eq_merged.to_csv(output_path, index=False)
        print(f"\n  Equity curve → {output_path}")

    # Save separate trade CSVs
    for label, suffix in [('with YURz', 'with_yurz'), ('without YURz', 'without_yurz')]:
        _, trds, _ = results.get(label, (pd.DataFrame(), [], {}))
        if trds:
            out_base = args.output.replace('.csv', '') if args.output.endswith('.csv') else args.output
            trades_path = f"{out_base}_trades_{suffix}.csv"
            if not os.path.isabs(trades_path):
                trades_path = os.path.join(SCRIPT_DIR, trades_path)
            pd.DataFrame(trds).to_csv(trades_path, index=False)
            print(f"  Trades ({label}) → {trades_path}")

    print(f"\n{'='*70}")
    print(f"  Done. {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
