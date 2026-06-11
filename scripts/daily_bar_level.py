#!/usr/bin/env python3
"""
Daily bar-level portfolio simulation with mark-to-market.
Operates on daily OHLCV bars: one bar = one trading day.
"""

import numpy as np
import pandas as pd


def _max_drawdown(equity):
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


class DailyPortfolio:
    """
    Bar-level portfolio for daily data on a single instrument (BR).
    
    Position sizing: contracts = capital * margin_usage / entry_price
    (1 contract = delta on 1 barrel, entry_price ~$80)
    """

    def __init__(self, margin_usage=0.10, stop_loss_pct=0.05,
                 max_hold_days=10, initial_capital=100000.0):
        self.margin_usage = margin_usage
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_days = max_hold_days
        self.initial_capital = initial_capital

    def run(self, daily_df, signals_df):
        capital = float(self.initial_capital)
        position = None
        trades = []
        equity_curve = []
        peak = self.initial_capital
        max_dd_lim = 0.20

        open_arr = daily_df['open'].values
        high_arr = daily_df['high'].values
        low_arr = daily_df['low'].values
        close_arr = daily_df['close'].values
        dates = daily_df.index
        n = len(dates)

        raw_signal = signals_df['signal'].values if 'signal' in signals_df.columns else np.zeros(n)
        pending_entry = False

        for i in range(n):
            op = open_arr[i]
            hi = high_arr[i]
            lo = low_arr[i]
            cl = close_arr[i]

            # Execute pending entry at today's open
            if pending_entry and position is None:
                entry_price = op
                cost_per_unit = entry_price
                max_risk = capital * self.margin_usage
                units = int(max_risk / cost_per_unit) if cost_per_unit > 0 else 0
                if units > 0 and cost_per_unit * units <= capital:
                    capital -= cost_per_unit * units
                    position = {
                        'entry_price': entry_price,
                        'entry_date': dates[i],
                        'bars_held': 0,
                        'highest': entry_price,
                        'units': units,
                    }
                pending_entry = False

            # Position management
            if position is not None:
                pos = position
                pos['bars_held'] += 1
                entry_price = pos['entry_price']
                highest = pos.get('highest', entry_price)
                units = pos['units']

                should_exit = False
                exit_price = cl
                exit_reason = None

                stop_level = entry_price * (1 - self.stop_loss_pct)
                if lo <= stop_level:
                    exit_price = min(stop_level, cl)
                    should_exit = True
                    exit_reason = 'stop_loss'

                if not should_exit and pos['bars_held'] >= self.max_hold_days:
                    exit_price = cl
                    should_exit = True
                    exit_reason = 'time_stop'

                if should_exit:
                    pnl = (exit_price - entry_price) * units
                    capital += entry_price * units + pnl
                    trades.append({
                        'entry_date': pos['entry_date'],
                        'exit_date': dates[i],
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'units': units,
                        'pnl': pnl,
                        'pnl_pct': pnl / (entry_price * units),
                        'reason': exit_reason,
                        'bars_held': pos['bars_held'],
                    })
                    position = None
                else:
                    if hi > highest:
                        position['highest'] = hi

            # Check entry for next day
            if position is None and not pending_entry:
                if i < n - 1 and raw_signal[i] == 1:
                    pending_entry = True

            # Equity
            current_equity = capital
            if position is not None:
                current_equity += position['entry_price'] * position['units']
                current_equity += (cl - position['entry_price']) * position['units']

            equity_curve.append(current_equity)

            if current_equity > peak:
                peak = current_equity
            dd = (peak - current_equity) / peak if peak > 0 else 0
            if dd > max_dd_lim:
                if position is not None:
                    pnl = (cl - position['entry_price']) * position['units']
                    capital += position['entry_price'] * position['units'] + pnl
                    trades.append({
                        'entry_date': position['entry_date'],
                        'exit_date': dates[i],
                        'entry_price': position['entry_price'],
                        'exit_price': cl,
                        'units': position['units'],
                        'pnl': pnl,
                        'pnl_pct': pnl / (position['entry_price'] * position['units']),
                        'reason': 'max_dd',
                        'bars_held': position['bars_held'],
                    })
                    position = None
                    equity_curve[-1] = capital
                break

        # Close any remaining
        if position is not None:
            pnl = (cl - position['entry_price']) * position['units']
            capital += position['entry_price'] * position['units'] + pnl
            trades.append({
                'entry_date': position['entry_date'],
                'exit_date': dates[-1],
                'entry_price': position['entry_price'],
                'exit_price': cl,
                'units': position['units'],
                'pnl': pnl,
                'pnl_pct': pnl / (position['entry_price'] * position['units']),
                'reason': 'end_of_data',
                'bars_held': position['bars_held'],
            })

        final_capital = capital
        total_return_pct = ((final_capital / self.initial_capital) - 1) * 100
        mdd = _max_drawdown(equity_curve)
        calmar = total_return_pct / (mdd * 100) if mdd > 0 else 0.0

        return {
            'final_capital': round(final_capital, 2),
            'total_return_pct': round(total_return_pct, 4),
            'max_dd_pct': round(mdd * 100, 4),
            'calmar': round(calmar, 4),
            'trades': trades,
            'equity_curve': equity_curve,
            'n_trades': len(trades),
        }
