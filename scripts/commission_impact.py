#!/usr/bin/env python3
"""
Direction 1: MOEX Commission Impact on BR SMA Mean Reversion.
Compares strategy performance with and without commission.
"""

import sys, os, json
from datetime import datetime

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.daily_bar_level import DailyPortfolio

DB_CONFIG = {
    'host': '10.0.0.64',
    'dbname': 'moex',
    'user': 'postgres',
    'password': 'postgres',
}

def fetch_daily_br():
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = 'BR'
        ORDER BY time
    """
    df = pd.read_sql(query, conn, parse_dates=['time'])
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

def main():
    print("=" * 70)
    print("НАПРАВЛЕНИЕ 1: ВЛИЯНИЕ КОМИССИЙ MOEX")
    print("=" * 70)

    daily = fetch_daily_br()
    print(f"\nData: {len(daily)} daily bars, {daily.index[0].date()} → {daily.index[-1].date()}")

    best_params = None
    best_calmar = -999
    from itertools import product
    for mu, hold, sl in product([0.10, 0.20, 0.50], [5, 10, 21], [0.03, 0.05, 0.10]):
        sig = generate_signals_sma(daily, 5, 20, 0.001, hold)
        sig['signal'] = sig['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], sig, mu, sl, hold, commission=0.0)
        if res['calmar'] > best_calmar:
            best_calmar = res['calmar']
            best_params = {'mu': mu, 'hold': hold, 'sl': sl}

    print(f"\nBest params (no commission): mu={best_params['mu']} hold={best_params['hold']} sl={best_params['sl']}")

    commissions = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0]
    rows = []
    for comm in commissions:
        sig = generate_signals_sma(daily, 5, 20, 0.001, best_params['hold'])
        sig['signal'] = sig['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], sig,
                      best_params['mu'], best_params['sl'],
                      best_params['hold'], commission=comm)
        rows.append({
            'commission': comm,
            'return_pct': res['total_return_pct'],
            'dd_pct': res['max_dd_pct'],
            'calmar': res['calmar'],
            'trades': res['n_trades'],
            'total_comm': res['total_commission'],
            'comm_pct_of_capital': res['total_commission'] / 100000.0 * 100,
        })
        print(f"  comm={comm:5.1f} RUB  ret={res['total_return_pct']:+8.4f}%  "
              f"DD={res['max_dd_pct']:.4f}%  Calmar={res['calmar']:.4f}  "
              f"trades={res['n_trades']}  comm_total={res['total_commission']:.2f}")

    base = rows[0]
    print(f"\n{'─' * 70}")
    print(f"Без комиссии:   {base['return_pct']:+.4f}%  DD={base['dd_pct']:.4f}%  Calmar={base['calmar']:.4f}")
    for r in rows[1:]:
        eaten = r['comm_pct_of_capital']
        ret_diff = r['return_pct'] - base['return_pct']
        print(f"Комиссия {r['commission']:.1f} RUB: ret={r['return_pct']:+.4f}% "
              f"(Δ={ret_diff:+.4f}%)  Calmar={r['calmar']:.4f}  съедено={eaten:.2f}% капитала")

    # Find at which commission Calmar drops below 1.0
    print(f"\n{'─' * 70}")
    print("АНАЛИЗ: При какой комиссии Calmar падает ниже 1.0?")
    for r in rows:
        status = "OK" if r['calmar'] >= 1.0 else "КРИТИЧНО"
        print(f"  comm={r['commission']:5.1f} RUB → Calmar={r['calmar']:.4f}  {status}")

    # Save report
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(report_dir, f'{date_str}-commission-impact.md')

    lines = [
        f"# Отчёт: Влияние комиссий MOEX на BR SMA Mean Reversion\n",
        f"**Дата:** {date_str}\n",
        f"**Данные:** {len(daily)} daily баров ({daily.index[0].date()} → {daily.index[-1].date()})\n",
        f"**Параметры:** mu={best_params['mu']}, hold={best_params['hold']}, sl={best_params['sl']}\n",
        f"\n## Результаты\n",
        f"\n| Комиссия (RUB) | Return% | DD% | Calmar | Сделок | Комиссия всего | % капитала |\n",
        f"|---|---|---|---|---|---|---|\n",
    ]
    for r in rows:
        lines.append(f"| {r['commission']} | {r['return_pct']:+.4f}% | {r['dd_pct']:.4f}% | {r['calmar']:.4f} | {r['trades']} | {r['total_comm']:.2f} | {r['comm_pct_of_capital']:.2f}% |\n")
    lines.append(f"\n## Вывод\n")
    lines.append(f"- Комиссия MOEX ~2 RUB/контракт\n")
    lines.append(f"- При 2 RUB: return={rows[3]['return_pct']:+.4f}%, Calmar={rows[3]['calmar']:.4f}\n")
    lines.append(f"- Без комиссии: return={base['return_pct']:+.4f}%, Calmar={base['calmar']:.4f}\n")

    with open(report_path, 'w') as f:
        f.writelines(lines)
    print(f"\nReport saved: {report_path}")

    return rows

if __name__ == '__main__':
    main()
