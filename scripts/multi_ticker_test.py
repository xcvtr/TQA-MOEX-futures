#!/usr/bin/env python3
"""
Direction 3: Multi-Ticker SMA Mean Reversion Test.
Tests SMA5 < SMA20 strategy on Si, RI, AU, ED, CNYRUBF, IMOEXF.
"""

import sys, os
from datetime import datetime
from itertools import product

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

TICKERS = ['Si', 'RI', 'AU', 'ED', 'CNYRUBF', 'IMOEXF']

def fetch_daily(symbol):
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s
        ORDER BY time
    """
    df = pd.read_sql(query, conn, parse_dates=['time'], params=[symbol])
    conn.close()
    df.set_index('time', inplace=True)
    df = df[~df.index.duplicated(keep='first')]
    ohlc_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    daily = df.resample('D').agg(ohlc_dict).dropna(subset=['close'])
    daily = daily[daily['volume'] > 0]
    return daily

def generate_signals_sma(daily_ohlcv, threshold=0.001, hold_days=5, sma_fast=5, sma_slow=20):
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

def run_sim(daily_ohlcv, sig_df, margin_usage=0.95, stop_loss_pct=0.10, max_hold_days=5):
    pf = DailyPortfolio(margin_usage=margin_usage, stop_loss_pct=stop_loss_pct,
                        max_hold_days=max_hold_days, initial_capital=100000.0)
    return pf.run(daily_ohlcv, sig_df[['signal']])

def walkforward_ticker(daily_ohlcv, param_grid):
    n = len(daily_ohlcv)
    fs = n // 4
    folds = [(0, fs), (fs, 2*fs), (2*fs, 3*fs), (3*fs, n)]
    results = []
    for hold in [5, 10, 21]:
        frs = []
        for fi, (f0, f1) in enumerate(folds):
            fd = daily_ohlcv.iloc[f0:f1].copy()
            if len(fd) < 5:
                frs.append({'fold': fi+1, 'return_pct': 0.0, 'max_dd_pct': 0.0, 'calmar': 0.0, 'n_signals': 0, 'trades': 0})
                continue
            sd = generate_signals_sma(fd, hold_days=hold)
            sd['signal'] = sd['signal'].clip(0, 1)
            ns = int(sd['signal'].sum())
            if ns < 1:
                frs.append({'fold': fi+1, 'return_pct': 0.0, 'max_dd_pct': 0.0, 'calmar': 0.0, 'n_signals': 0, 'trades': 0})
                continue
            r = run_sim(fd, sd, 0.95, 0.10, hold)
            frs.append({'fold': fi+1, 'return_pct': r['total_return_pct'], 'max_dd_pct': r['max_dd_pct'],
                        'calmar': r['calmar'], 'n_signals': ns, 'trades': len(r['trades'])})
        ap = all(fr['return_pct'] > 0 for fr in frs if fr['n_signals'] > 0)
        results.append({'params': {'hold': hold}, 'fold_results': frs, 'all_profitable': ap})
    return results

def main():
    print("=" * 70)
    print("НАПРАВЛЕНИЕ 3: ТЕСТ SMA MEAN REVERSION НА ДРУГИХ ИНСТРУМЕНТАХ")
    print("=" * 70)
    print(f"\nСигнал: SMA5 < SMA20 → LONG, hold=5, sl=0.10, mu=0.95")
    print(f"Walk-forward: 4 folds\n")

    all_results = []
    for ticker in TICKERS:
        daily = fetch_daily(ticker)
        print(f"{'─' * 70}")
        print(f"TICKER: {ticker}  ({len(daily)} bars, {daily.index[0].date()} → {daily.index[-1].date()})")

        sig = generate_signals_sma(daily, threshold=0.001, hold_days=5)
        sig['signal'] = sig['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], sig, 0.95, 0.10, 5)

        wr = sum(1 for t in res['trades'] if t['pnl'] > 0) / max(res['n_trades'], 1) * 100

        # Walk-forward for hold=5
        wf = walkforward_ticker(daily, [5])
        wf_profitable = sum(1 for r in wf if r['all_profitable'])

        print(f"  Return: {res['total_return_pct']:+.4f}%  DD: {res['max_dd_pct']:.4f}%  "
              f"Calmar: {res['calmar']:.4f}  Trades: {res['n_trades']}  WR: {wr:.1f}%")
        print(f"  WF profitable: {wf_profitable}/{len(wf)}")

        all_results.append({
            'ticker': ticker,
            'bars': len(daily),
            'return_pct': res['total_return_pct'],
            'dd_pct': res['max_dd_pct'],
            'calmar': res['calmar'],
            'trades': res['n_trades'],
            'wr': wr,
            'wf_pass': wf_profitable,
        })

    # Results table
    print(f"\n{'=' * 90}")
    print(f"{'Ticker':10s}  {'Bars':>6s}  {'Return%':>10s}  {'DD%':>8s}  {'Calmar':>8s}  {'Trades':>7s}  {'WR%':>7s}  {'WF pass':>8s}")
    print(f"{'─' * 90}")
    for r in sorted(all_results, key=lambda x: x['calmar'], reverse=True):
        print(f"{r['ticker']:10s}  {r['bars']:>6d}  {r['return_pct']:>+8.4f}%  {r['dd_pct']:>7.4f}%  "
              f"{r['calmar']:>8.4f}  {r['trades']:>7d}  {r['wr']:>6.1f}%  {r['wf_pass']:>3d}/{len(TICKERS) or 1}")

    best = sorted(all_results, key=lambda x: x['calmar'], reverse=True)

    # Save report
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(report_dir, f'{date_str}-multi-ticker-test.md')
    lines = [
        f"# Отчёт: Multi-Ticker SMA Mean Reversion\n",
        f"**Дата:** {date_str}\n",
        f"**Параметры:** SMA5 < SMA20 → LONG, hold=5, sl=0.10, mu=0.50\n",
        f"\n## Результаты\n",
        f"\n| Ticker | Bars | Return% | DD% | Calmar | Trades | WR% | WF pass |\n",
        f"|---|---|---|---|---|---|---|---|\n",
    ]
    for r in best:
        lines.append(f"| {r['ticker']} | {r['bars']} | {r['return_pct']:+.4f}% | {r['dd_pct']:.4f}% | {r['calmar']:.4f} | {r['trades']} | {r['wr']:.1f}% | {r['wf_pass']}/{len(TICKERS)} |\n")
    lines.append(f"\n## Топ инструментов (Calmar > 1.0)\n")
    top = [r for r in best if r['calmar'] > 1.0]
    for r in top:
        lines.append(f"- {r['ticker']}: Calmar={r['calmar']:.4f}, Return={r['return_pct']:+.4f}%, DD={r['dd_pct']:.4f}%\n")
    if not top:
        lines.append("Ни один инструмент не показал Calmar > 1.0\n")
    with open(report_path, 'w') as f:
        f.writelines(lines)
    print(f"\nReport saved: {report_path}")

    return all_results

if __name__ == '__main__':
    main()
