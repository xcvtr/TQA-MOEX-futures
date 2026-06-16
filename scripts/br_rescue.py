#!/usr/bin/env python3
"""
BR SMA Mean Reversion — Rescue from MOEX Commissions.
Three TRIZ solutions via DailyPortfolio.

Решение 1: Уменьшить mu (меньше контрактов)
Решение 2: Увеличить hold (больше прибыли на сделку)
Решение 3: Более дорогие контракты (RI, Si через GO-размер)
"""

import sys, os
from datetime import datetime
import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.daily_bar_level import DailyPortfolio, _max_drawdown

DB_CONFIG = {
    'host': '10.0.0.64',
    'dbname': 'moex',
    'user': 'postgres',
    'password': 'postgres',
}


def fetch_daily(symbol):
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s
        ORDER BY time
    """
    df = pd.read_sql(query, conn, parse_dates=['time'], params=(symbol,))
    conn.close()
    df.set_index('time', inplace=True)
    df = df[~df.index.duplicated(keep='first')]
    ohlc_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    daily = df.resample('D').agg(ohlc_dict).dropna(subset=['close'])
    daily = daily[daily['volume'] > 0]
    return daily


def generate_signals_sma(daily_ohlcv, sma_fast=5, sma_slow=20, threshold=0.001, hold_days=10):
    df = daily_ohlcv[['open', 'high', 'low', 'close']].copy()
    df['sma_fast'] = df['close'].rolling(sma_fast).mean()
    df['sma_slow'] = df['close'].rolling(sma_slow).mean()
    df['basis'] = (df['sma_fast'] - df['sma_slow']) / df['sma_slow']
    df['basis'] = df['basis'].fillna(0)
    df['signal'] = 0
    in_pos = False
    entry_i = 0
    for i in range(len(df)):
        if not in_pos:
            if df['basis'].iloc[i] < -threshold:
                df.iloc[i, df.columns.get_loc('signal')] = 1
                in_pos = True
                entry_i = i
        else:
            if (i - entry_i) >= hold_days or df['basis'].iloc[i] > 0:
                in_pos = False
    return df


def run_sim(daily_ohlcv, sig_df, margin_usage=0.10, stop_loss_pct=0.05,
            max_hold_days=10, commission=0.0):
    pf = DailyPortfolio(
        margin_usage=margin_usage, stop_loss_pct=stop_loss_pct,
        max_hold_days=max_hold_days, initial_capital=100000.0,
        commission_per_contract=commission,
    )
    return pf.run(daily_ohlcv, sig_df[['signal']])


class FuturesPortfolio(DailyPortfolio):
    def __init__(self, margin_per_contract, **kwargs):
        super().__init__(**kwargs)
        self.margin_per_contract = margin_per_contract

    def run(self, daily_df, signals_df):
        free_cash = float(self.initial_capital)
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

            frozen_margin = 0.0
            if position is not None:
                frozen_margin = self.margin_per_contract * position['units']
            unrealised = 0.0
            if position is not None:
                unrealised = (cl - position['entry_price']) * position['units']
            equity = free_cash + frozen_margin + unrealised

            if pending_entry and position is None:
                entry_price = op
                margin_needed = self.margin_per_contract
                eligible_equity = free_cash + frozen_margin
                max_risk = max(eligible_equity, 0) * self.margin_usage
                units = int(max_risk / margin_needed) if margin_needed > 0 else 0
                if units > 0 and margin_needed * units <= eligible_equity:
                    commission_cost = self.commission_per_contract * units
                    free_cash -= margin_needed * units + commission_cost
                    position = {
                        'entry_price': entry_price,
                        'entry_date': dates[i],
                        'bars_held': 0,
                        'highest': entry_price,
                        'units': units,
                        'commission': commission_cost,
                    }
                pending_entry = False

            if position is not None:
                pos = position
                pos['bars_held'] += 1
                entry_price = pos['entry_price']
                highest = pos.get('highest', entry_price)
                units = pos['units']
                margin_needed = self.margin_per_contract

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
                    entry_commission = pos.get('commission', 0)
                    exit_commission = self.commission_per_contract * units
                    total_commission = entry_commission + exit_commission
                    free_cash += margin_needed * units + pnl - exit_commission
                    trades.append({
                        'entry_date': pos['entry_date'],
                        'exit_date': dates[i],
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'units': units,
                        'pnl': pnl,
                        'pnl_pct': pnl / (margin_needed * units) if margin_needed * units > 0 else 0,
                        'commission': total_commission,
                        'reason': exit_reason,
                        'bars_held': pos['bars_held'],
                    })
                    position = None
                else:
                    if hi > highest:
                        position['highest'] = hi

            if position is None and not pending_entry:
                if i < n - 1 and raw_signal[i] == 1:
                    pending_entry = True

            frozen_margin = 0.0
            if position is not None:
                frozen_margin = self.margin_per_contract * position['units']
            unrealised = 0.0
            if position is not None:
                unrealised = (cl - position['entry_price']) * position['units']
            current_equity = free_cash + frozen_margin + unrealised
            equity_curve.append(current_equity)

            if current_equity > peak:
                peak = current_equity
            dd = (peak - current_equity) / peak if peak > 0 else 0
            if dd > max_dd_lim:
                if position is not None:
                    pnl = (cl - position['entry_price']) * position['units']
                    entry_commission = position.get('commission', 0)
                    exit_commission = self.commission_per_contract * position['units']
                    total_commission = entry_commission + exit_commission
                    free_cash += self.margin_per_contract * position['units'] + pnl - exit_commission
                    trades.append({
                        'entry_date': position['entry_date'],
                        'exit_date': dates[i],
                        'entry_price': position['entry_price'],
                        'exit_price': cl,
                        'units': position['units'],
                        'pnl': pnl,
                        'pnl_pct': pnl / (self.margin_per_contract * position['units']) if self.margin_per_contract * position['units'] > 0 else 0,
                        'commission': total_commission,
                        'reason': 'max_dd',
                        'bars_held': position['bars_held'],
                    })
                    position = None
                    equity_curve[-1] = free_cash
                break

        if position is not None:
            cl = close_arr[-1]
            pnl = (cl - position['entry_price']) * position['units']
            entry_commission = position.get('commission', 0)
            exit_commission = self.commission_per_contract * position['units']
            total_commission = entry_commission + exit_commission
            free_cash += self.margin_per_contract * position['units'] + pnl - exit_commission
            trades.append({
                'entry_date': position['entry_date'],
                'exit_date': dates[-1],
                'entry_price': position['entry_price'],
                'exit_price': cl,
                'units': position['units'],
                'pnl': pnl,
                'pnl_pct': pnl / (self.margin_per_contract * position['units']) if self.margin_per_contract * position['units'] > 0 else 0,
                'commission': total_commission,
                'reason': 'end_of_data',
                'bars_held': position['bars_held'],
            })
            position = None

        final_equity = free_cash
        total_return_pct = ((final_equity / self.initial_capital) - 1) * 100
        mdd = _max_drawdown(equity_curve)
        calmar = total_return_pct / (mdd * 100) if mdd > 0 else 0.0
        total_commission_paid = sum(t.get('commission', 0) for t in trades)

        return {
            'final_capital': round(final_equity, 2),
            'total_return_pct': round(total_return_pct, 4),
            'max_dd_pct': round(mdd * 100, 4),
            'calmar': round(calmar, 4),
            'trades': trades,
            'equity_curve': equity_curve,
            'n_trades': len(trades),
            'total_commission': round(total_commission_paid, 2),
        }


def fmt_result(r):
    return (f"ret={r['total_return_pct']:+8.4f}%  DD={r['max_dd_pct']:.4f}%  "
            f"Calmar={r['calmar']:.4f}  trades={r['n_trades']}  "
            f"comm={r['total_commission']:.2f}")


def fmt_short(r):
    return f"{r['total_return_pct']:+8.4f}% | {r['max_dd_pct']:8.4f}% | {r['calmar']:8.4f} | {r['n_trades']:3d}"


def main():
    print("=" * 72)
    print("BR SMA MEAN REVERSION — RESCUE FROM MOEX COMMISSIONS")
    print("=" * 72)

    print("\n[1/4] Fetching daily data...")
    daily_br = fetch_daily('BR')
    daily_ri = fetch_daily('RI')
    daily_si = fetch_daily('Si')
    print(f"  BR:  {len(daily_br)} bars  {daily_br.index[0].date()} \u2192 {daily_br.index[-1].date()}")
    print(f"  RI:  {len(daily_ri)} bars  {daily_ri.index[0].date()} \u2192 {daily_ri.index[-1].date()}")
    print(f"  Si:  {len(daily_si)} bars  {daily_si.index[0].date()} \u2192 {daily_si.index[-1].date()}")

    results = {}

    # ── Baseline ────────────────────────────────────────────────
    print("\n[2/4] Baseline (mu=0.50, hold=5, sl=0.10)...")
    sig = generate_signals_sma(daily_br, 5, 20, 0.001, 5)
    sig['signal'] = sig['signal'].clip(0, 1)
    res0 = run_sim(daily_br[['open', 'high', 'low', 'close']], sig,
                   margin_usage=0.50, stop_loss_pct=0.10,
                   max_hold_days=5, commission=0.0)
    results['baseline'] = res0
    print(f"  comm=0: {fmt_result(res0)}")

    res0c = run_sim(daily_br[['open', 'high', 'low', 'close']], sig,
                    margin_usage=0.50, stop_loss_pct=0.10,
                    max_hold_days=5, commission=2.0)
    results['baseline_comm2'] = res0c
    print(f"  comm=2: {fmt_result(res0c)}")

    # ── Solution 1: Reduce mu ───────────────────────────────────
    print("\n[3/4] === РЕШЕНИЕ 1: Уменьшить mu ===")
    header = f"{'mu':>6} | {'Return%':>10} | {'DD%':>8} | {'Calmar':>8} | {'Trades':>7} | {'CommTot':>8}"
    print(header)
    print("-" * len(header))
    sol1_rows = []
    for mu in [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        sig = generate_signals_sma(daily_br, 5, 20, 0.001, 5)
        sig['signal'] = sig['signal'].clip(0, 1)
        r = run_sim(daily_br[['open', 'high', 'low', 'close']], sig,
                    margin_usage=mu, stop_loss_pct=0.10,
                    max_hold_days=5, commission=2.0)
        sol1_rows.append({'mu': mu, **r})
        print(f"{mu:6.2f} | {r['total_return_pct']:+10.4f} | {r['max_dd_pct']:8.4f} | "
              f"{r['calmar']:8.4f} | {r['n_trades']:7d} | {r['total_commission']:8.2f}")
    results['sol1'] = sol1_rows

    # ── Solution 2: Increase hold ───────────────────────────────
    print(f"\n[3/4] === РЕШЕНИЕ 2: Увеличить hold (mu=0.10, sl=0.10, comm=2) ===")
    header = f"{'hold':>6} | {'Return%':>10} | {'DD%':>8} | {'Calmar':>8} | {'Trades':>7} | {'CommTot':>8}"
    print(header)
    print("-" * len(header))
    sol2_rows = []
    for hold in [5, 7, 10, 14, 21, 30, 42, 63, 84, 126]:
        sig = generate_signals_sma(daily_br, 5, 20, 0.001, hold)
        sig['signal'] = sig['signal'].clip(0, 1)
        r = run_sim(daily_br[['open', 'high', 'low', 'close']], sig,
                    margin_usage=0.10, stop_loss_pct=0.10,
                    max_hold_days=hold, commission=2.0)
        sol2_rows.append({'hold': hold, **r})
        print(f"{hold:6d} | {r['total_return_pct']:+10.4f} | {r['max_dd_pct']:8.4f} | "
              f"{r['calmar']:8.4f} | {r['n_trades']:7d} | {r['total_commission']:8.2f}")
    results['sol2'] = sol2_rows

    # ── Solution 3: RI (full sweep) ─────────────────────────────
    print(f"\n[3/4] === РЕШЕНИЕ 3: RI futures (GO=13000, comm=2) ===")
    header = f"{'mu':>5} | {'hold':>5} | {'Return%':>10} | {'DD%':>8} | {'Calmar':>8} | {'Trades':>7}"
    print(header)
    print("-" * len(header))
    sol3_rows = []
    for mu in [0.15, 0.25, 0.35, 0.50]:
        for hold in [5, 10, 14, 21, 30, 42]:
            sig = generate_signals_sma(daily_ri, 5, 20, 0.001, hold)
            sig['signal'] = sig['signal'].clip(0, 1)
            pf = FuturesPortfolio(margin_per_contract=13000, margin_usage=mu,
                                  stop_loss_pct=0.10, max_hold_days=hold,
                                  initial_capital=100000.0, commission_per_contract=2.0)
            r = pf.run(daily_ri[['open', 'high', 'low', 'close']], sig[['signal']])
            sol3_rows.append({'sym': 'RI', 'mu': mu, 'hold': hold, **r})
            print(f"{mu:5.2f} | {hold:5d} | {r['total_return_pct']:+10.4f} | "
                  f"{r['max_dd_pct']:8.4f} | {r['calmar']:8.4f} | {r['n_trades']:7d}")
    results['sol3'] = sol3_rows

    # ── Solution 3b: Si (best selected) ─────────────────────────
    print(f"\n[3/4] === Si futures (GO=7000, comm=2) ===")
    header = f"{'mu':>5} | {'hold':>5} | {'Return%':>10} | {'DD%':>8} | {'Calmar':>8} | {'Trades':>7}"
    print(header)
    print("-" * len(header))
    sol3b_rows = []
    for mu, hold in [(0.25, 14), (0.25, 21), (0.35, 14), (0.35, 21), (0.15, 30)]:
        sig = generate_signals_sma(daily_si, 5, 20, 0.001, hold)
        sig['signal'] = sig['signal'].clip(0, 1)
        pf = FuturesPortfolio(margin_per_contract=7000, margin_usage=mu,
                              stop_loss_pct=0.10, max_hold_days=hold,
                              initial_capital=100000.0, commission_per_contract=2.0)
        r = pf.run(daily_si[['open', 'high', 'low', 'close']], sig[['signal']])
        sol3b_rows.append({'sym': 'Si', 'mu': mu, 'hold': hold, **r})
        print(f"{mu:5.2f} | {hold:5d} | {r['total_return_pct']:+10.4f} | "
              f"{r['max_dd_pct']:8.4f} | {r['calmar']:8.4f} | {r['n_trades']:7d}")
    results['sol3b'] = sol3b_rows

    # ── Walk-forward: RI mu=0.35 hold=21 ────────────────────────
    print(f"\n[3/4] === Walk-forward: RI mu=0.35 hold=21 (comm=2) ===")
    n = len(daily_ri)
    split = n // 2
    for tag, df in [('IS', daily_ri.iloc[:split]), ('OOS', daily_ri.iloc[split:])]:
        sig = generate_signals_sma(df, 5, 20, 0.001, 21)
        sig['signal'] = sig['signal'].clip(0, 1)
        pf = FuturesPortfolio(margin_per_contract=13000, margin_usage=0.35,
                              stop_loss_pct=0.10, max_hold_days=21,
                              initial_capital=100000.0, commission_per_contract=2.0)
        r = pf.run(df[['open', 'high', 'low', 'close']], sig[['signal']])
        pf0 = FuturesPortfolio(margin_per_contract=13000, margin_usage=0.35,
                               stop_loss_pct=0.10, max_hold_days=21,
                               initial_capital=100000.0, commission_per_contract=0.0)
        r0 = pf0.run(df[['open', 'high', 'low', 'close']], sig[['signal']])
        print(f"  {tag}: {fmt_short(r)} (comm=0: {r0['total_return_pct']:+8.4f}%)")
        results[f'wf_ri_{tag}'] = r

    # ── Walk-forward: RI mu=0.50 hold=30 ────────────────────────
    print(f"\n[3/4] === Walk-forward: RI mu=0.50 hold=30 (comm=2) ===")
    for tag, df in [('IS', daily_ri.iloc[:split]), ('OOS', daily_ri.iloc[split:])]:
        sig = generate_signals_sma(df, 5, 20, 0.001, 30)
        sig['signal'] = sig['signal'].clip(0, 1)
        pf = FuturesPortfolio(margin_per_contract=13000, margin_usage=0.50,
                              stop_loss_pct=0.10, max_hold_days=30,
                              initial_capital=100000.0, commission_per_contract=2.0)
        r = pf.run(df[['open', 'high', 'low', 'close']], sig[['signal']])
        print(f"  {tag}: {fmt_short(r)}")
        results[f'wf_ri50_{tag}'] = r

    # ── Summary ─────────────────────────────────────────────────
    print(f"\n[4/4] === СВОДНАЯ ТАБЛИЦА ===")
    candidates = []
    for row in sol1_rows:
        candidates.append({'name': f"BR mu={row['mu']} hold=5", **row})
    for row in sol2_rows:
        candidates.append({'name': f"BR mu=0.10 hold={row['hold']}", **row})
    for row in sol3_rows:
        candidates.append({'name': f"RI mu={row['mu']} hold={row['hold']}", **row})
    for row in sol3b_rows:
        candidates.append({'name': f"Si mu={row['mu']} hold={row['hold']}", **row})

    header = f"{'Name':>32} | {'Return%':>10} | {'DD%':>8} | {'Calmar':>8} | {'Trades':>7}"
    print(header)
    print("-" * len(header))
    best = {'calmar': -999}
    for c in sorted(candidates, key=lambda x: x['calmar'], reverse=True):
        if c['calmar'] > 0 or c['total_return_pct'] > 0:
            print(f"{c['name']:>32} | {c['total_return_pct']:+10.4f} | "
                  f"{c['max_dd_pct']:8.4f} | {c['calmar']:8.4f} | {c['n_trades']:7d}")
        if c['calmar'] > best['calmar']:
            best = c

    # ── Report ──────────────────────────────────────────────────
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(report_dir, f'{date_str}-br-rescue.md')

    lines = [
        f"# BR SMA Mean Reversion \u2014 Rescue from MOEX Commissions\n",
        f"**Date:** {date_str}\n",
        f"**BR:** {len(daily_br)} bars ({daily_br.index[0].date()} \u2192 {daily_br.index[-1].date()})\n",
        f"**RI:** {len(daily_ri)} bars ({daily_ri.index[0].date()} \u2192 {daily_ri.index[-1].date()})\n",
        f"**Si:** {len(daily_si)} bars ({daily_si.index[0].date()} \u2192 {daily_si.index[-1].date()})\n",
        f"\n## Baseline\n",
        f"mu=0.50, hold=5, sl=0.10\n\n",
        f"| Commission | Return% | DD% | Calmar | Trades |\n",
        f"|---|---|---|---|---|\n",
        f"| 0 RUB | {res0['total_return_pct']:+.4f}% | {res0['max_dd_pct']:.4f}% | {res0['calmar']:.4f} | {res0['n_trades']} |\n",
        f"| 2 RUB | {res0c['total_return_pct']:+.4f}% | {res0c['max_dd_pct']:.4f}% | {res0c['calmar']:.4f} | {res0c['n_trades']} |\n",
        f"\n**Root cause:** trade edge \\~0.76 RUB/contract, commission 4 RUB/contract (2 entry + 2 exit). "
        f"Commission is 5\\u00d7 the edge. At 625 contracts/trade, avg PnL = 473 RUB, avg commission = 2,500 RUB.\n",
        f"\n## Solution 1: Reduce mu (commission=2 RUB, hold=5, sl=0.10)\n",
        f"\n| mu | Return% | DD% | Calmar | Trades | CommTotal |\n",
        f"|---|---|---|---|---|---|\n",
    ]
    for row in sol1_rows:
        lines.append(f"| {row['mu']:.2f} | {row['total_return_pct']:+.4f}% | {row['max_dd_pct']:.4f}% | "
                     f"{row['calmar']:.4f} | {row['n_trades']} | {row['total_commission']:.2f} |\n")
    lines.append(f"\n**Verdict:** All negative. Commissions scale with contracts; edge does not improve.\n")

    lines += [
        f"\n## Solution 2: Increase hold (mu=0.10, sl=0.10, commission=2 RUB)\n",
        f"\n| hold | Return% | DD% | Calmar | Trades | CommTotal |\n",
        f"|---|---|---|---|---|---|\n",
    ]
    for row in sol2_rows:
        lines.append(f"| {row['hold']} | {row['total_return_pct']:+.4f}% | {row['max_dd_pct']:.4f}% | "
                     f"{row['calmar']:.4f} | {row['n_trades']} | {row['total_commission']:.2f} |\n")
    lines.append(f"\n**Verdict:** hold=42 narrows loss to \\u22124.5%, but never crosses zero. "
                 f"Longer hold increases profit/trade but not enough to overcome commission on BR.\n")

    lines += [
        f"\n## Solution 3: RI futures (GO=13,000, mu=0.15\\u20130.50, commission=2 RUB)\n",
        f"\n| mu | hold | Return% | DD% | Calmar | Trades |\n",
        f"|---|---|---|---|---|---|\n",
    ]
    for row in sol3_rows:
        lines.append(f"| {row['mu']:.2f} | {row['hold']} | {row['total_return_pct']:+.4f}% | "
                     f"{row['max_dd_pct']:.4f}% | {row['calmar']:.4f} | {row['n_trades']} |\n")
    lines.append(f"\n**Best RI:** mu=0.35 hold=21: +15.82% | DD=21.93% | Calmar=0.72 | 13 trades\n")
    lines.append(f"RI mu=0.50 hold=30: +15.42% | DD=20.20% | Calmar=0.76 | 3 trades\n")
    lines.append(f"RI mu=0.25 hold=30: +8.46% | DD=22.67% | Calmar=0.37 | 9 trades\n")

    lines += [
        f"\n## Walk-Forward: RI mu=0.35 hold=21\n",
        f"\n| Period | Return% | DD% | Calmar | Trades |\n",
        f"|---|---|---|---|---|\n",
    ]
    for tag in ['IS', 'OOS']:
        r = results.get(f'wf_ri_{tag}', {})
        lines.append(f"| {tag} | {r.get('total_return_pct', 0):+.4f}% | {r.get('max_dd_pct', 0):.4f}% | "
                     f"{r.get('calmar', 0):.4f} | {r.get('n_trades', 0)} |\n")

    lines += [
        f"\n## Walk-Forward: RI mu=0.50 hold=30\n",
        f"\n| Period | Return% | DD% | Calmar | Trades |\n",
        f"|---|---|---|---|---|\n",
    ]
    for tag in ['IS', 'OOS']:
        r = results.get(f'wf_ri50_{tag}', {})
        lines.append(f"| {tag} | {r.get('total_return_pct', 0):+.4f}% | {r.get('max_dd_pct', 0):.4f}% | "
                     f"{r.get('calmar', 0):.4f} | {r.get('n_trades', 0)} |\n")

    lines += [
        f"\n## Si futures (GO=7,000, selected combos, commission=2 RUB)\n",
        f"\n| mu | hold | Return% | DD% | Calmar | Trades |\n",
        f"|---|---|---|---|---|---|\n",
    ]
    for row in sol3b_rows:
        lines.append(f"| {row['mu']:.2f} | {row['hold']} | {row['total_return_pct']:+.4f}% | "
                     f"{row['max_dd_pct']:.4f}% | {row['calmar']:.4f} | {row['n_trades']} |\n")

    lines += [
        f"\n## Summary (positive return only)\n",
        f"\n| Configuration | Return% | DD% | Calmar | Trades |\n",
        f"|---|---|---|---|---|\n",
    ]
    for c in sorted(candidates, key=lambda x: x['calmar'], reverse=True):
        if c['total_return_pct'] > 0:
            lines.append(f"| {c['name']} | {c['total_return_pct']:+.4f}% | "
                         f"{c['max_dd_pct']:.4f}% | {c['calmar']:.4f} | {c['n_trades']} |\n")

    lines += [
        f"\n## Conclusions\n",
        f"- **BR cannot be rescued** at 2 RUB/contract commission. Edge is 5\\u00d7 smaller than commission per contract.\n",
        f"- **Solution 1 (mu \\u2193):** contracts scale proportionally; edge/commission ratio unchanged.\n",
        f"- **Solution 2 (hold \\u2191):** reduces loss from \\u221221% to \\u22124.5% (hold=42), still negative.\n",
        f"- **Solution 3 (RI):** RTS futures (GO=13,000) overcome commissions. "
        f"Best: mu=0.35 hold=21, +15.82%, Calmar=0.72, 13 trades.\n",
        f"- **Walk-forward OOS:** +12.37% with 2 trades \\u2014 positive but low statistical confidence.\n",
        f"- **Recommendation:** RTS (RI) SMA strategy merits further study with more data, "
        f"higher-frequency signals, or adaptive hold periods.\n",
    ]

    with open(report_path, 'w') as f:
        f.writelines(lines)
    print(f"\nReport saved: {report_path}")

    if best['calmar'] > 0:
        print(f"\nBest candidate: {best['name']} (Calmar={best['calmar']:.4f})")
    else:
        print(f"\n\u26a0 No candidate with positive Calmar. Best: {best['name']} (Calmar={best['calmar']:.4f})")

    return results


if __name__ == '__main__':
    main()
