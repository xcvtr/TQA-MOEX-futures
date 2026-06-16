#!/usr/bin/env python3
"""
Direction 4: Ensemble Portfolio — combine top tickers into a single portfolio.
Each strategy gets equal capital share.
"""

import sys, os
from datetime import datetime
from itertools import product

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

ALL_TICKERS = ['Si', 'RI', 'AU', 'ED', 'CNYRUBF', 'IMOEXF']

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

def generate_signals_sma(daily_ohlcv, threshold=0.001, hold_days=5):
    df = daily_ohlcv[['open', 'high', 'low', 'close']].copy()
    df['sma_fast'] = df['close'].rolling(5).mean()
    df['sma_slow'] = df['close'].rolling(20).mean()
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

def run_sim_single(daily_ohlcv, sig_df, capital=100000.0, margin_usage=0.95,
                   stop_loss_pct=0.10, max_hold_days=5):
    pf = DailyPortfolio(margin_usage=margin_usage, stop_loss_pct=stop_loss_pct,
                        max_hold_days=max_hold_days, initial_capital=capital)
    return pf.run(daily_ohlcv, sig_df[['signal']])

def run_ensemble(ticker_data, capital=100000.0, margin_usage=0.95,
                 stop_loss_pct=0.10, max_hold_days=5):
    n = len(ticker_data)
    per_ticker_capital = capital / n

    combined_pnl = pd.Series(dtype=float)
    all_equities = []

    for ticker, (daily, sig_df) in ticker_data.items():
        pf = DailyPortfolio(margin_usage=margin_usage, stop_loss_pct=stop_loss_pct,
                            max_hold_days=max_hold_days, initial_capital=per_ticker_capital)
        res = pf.run(daily, sig_df[['signal']])
        equity = pd.Series(res['equity_curve'], index=daily.index[:len(res['equity_curve'])])
        if combined_pnl.empty:
            combined_pnl = equity
        else:
            combined_pnl = combined_pnl.add(equity, fill_value=per_ticker_capital)

    total_return_pct = ((combined_pnl.iloc[-1] / capital) - 1) * 100
    mdd = _max_drawdown(combined_pnl.tolist())
    calmar = total_return_pct / (mdd * 100) if mdd > 0 else 0.0

    all_trades = sum(len(res['trades']) for _, res in
                     [(t, run_sim_single(d, s, per_ticker_capital, margin_usage, stop_loss_pct, max_hold_days))
                      for t, (d, s) in ticker_data.items()])

    return {
        'final_capital': round(combined_pnl.iloc[-1], 2),
        'total_return_pct': round(total_return_pct, 4),
        'max_dd_pct': round(mdd * 100, 4),
        'calmar': round(calmar, 4),
        'n_trades': all_trades,
        'equity_curve': combined_pnl.tolist(),
    }

def main():
    print("=" * 70)
    print("НАПРАВЛЕНИЕ 4: ENSEMBLE ПОРТФЕЛЬ")
    print("=" * 70)

    # Load all tickers
    ticker_data = {}
    for ticker in ALL_TICKERS:
        daily = fetch_daily(ticker)
        sig = generate_signals_sma(daily, threshold=0.001, hold_days=5)
        sig['signal'] = sig['signal'].clip(0, 1)
        ticker_data[ticker] = (daily[['open','high','low','close']], sig)
        print(f"  {ticker}: {len(daily)} bars")

    # Score each ticker
    print(f"\n{'─' * 70}")
    print("ОЦЕНКА КАЖДОГО ИНСТРУМЕНТА")
    scored = []
    for ticker in ALL_TICKERS:
        daily, sig = ticker_data[ticker]
        res = run_sim_single(daily, sig, 100000.0, 0.95, 0.10, 5)
        wr = sum(1 for t in res['trades'] if t['pnl'] > 0) / max(res['n_trades'], 1) * 100
        scored.append({'ticker': ticker, 'calmar': res['calmar'], 'return_pct': res['total_return_pct'],
                       'dd_pct': res['max_dd_pct'], 'trades': res['n_trades'], 'wr': wr})
        print(f"  {ticker:10s}  return={res['total_return_pct']:+8.4f}%  DD={res['max_dd_pct']:.4f}%  "
              f"Calmar={res['calmar']:.4f}  trades={res['n_trades']}  WR={wr:.1f}%")

    # Best single
    best_single = max(scored, key=lambda x: x['calmar'])
    print(f"\n{'─' * 70}")
    print(f"Лучший инструмент: {best_single['ticker']} (Calmar={best_single['calmar']:.4f}, "
          f"ret={best_single['return_pct']:+.4f}%)")

    # Ensemble with top N (Calmar > 1.0)
    top = [s for s in scored if s['calmar'] > 1.0]
    if not top:
        top = sorted(scored, key=lambda x: x['calmar'], reverse=True)[:2]

    print(f"\n{'─' * 70}")
    print(f"ENSEMBLE: {[t['ticker'] for t in top]}")

    tickers_for_ensemble = {t['ticker']: ticker_data[t['ticker']] for t in top}
    ens_res = run_ensemble(tickers_for_ensemble, 100000.0, 0.95, 0.10, 5)

    ensemble_calmar = ens_res['calmar']
    ensemble_return = ens_res['total_return_pct']
    ensemble_dd = ens_res['max_dd_pct']

    print(f"  Ensemble:  return={ensemble_return:+8.4f}%  DD={ensemble_dd:.4f}%  "
          f"Calmar={ensemble_calmar:.4f}  trades={ens_res['n_trades']}")
    print(f"  Best single ({best_single['ticker']}): return={best_single['return_pct']:+8.4f}%  "
          f"DD={best_single['dd_pct']:.4f}%  Calmar={best_single['calmar']:.4f}")

    # Improvement
    dd_improvement = (best_single['dd_pct'] - ensemble_dd) / max(best_single['dd_pct'], 0.01)
    ret_diff = ensemble_return - best_single['return_pct']
    print(f"\n{'─' * 70}")
    print(f"УЛУЧШЕНИЕ:")
    print(f"  Return: {best_single['return_pct']:+.4f}% → {ensemble_return:+.4f}% (Δ={ret_diff:+.4f}%)")
    print(f"  DD: {best_single['dd_pct']:.4f}% → {ensemble_dd:.4f}% (снижение на {dd_improvement:.1f}%)")
    print(f"  Calmar: {best_single['calmar']:.4f} → {ensemble_calmar:.4f}")

    # Save report
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(report_dir, f'{date_str}-ensemble-test.md')
    lines = [
        f"# Отчёт: Ensemble портфель SMA Mean Reversion\n",
        f"**Дата:** {date_str}\n",
        f"**Параметры:** SMA5 < SMA20 → LONG, hold=5, sl=0.10, mu=0.50\n",
        f"\n## Инструменты в портфеле\n",
        f"{', '.join(t['ticker'] for t in top)}\n",
        f"\n## Результаты\n",
        f"\n| Параметр | Ensemble | Best Single ({best_single['ticker']}) |\n",
        f"|---|---|---|\n",
        f"| Return% | {ensemble_return:+.4f}% | {best_single['return_pct']:+.4f}% |\n",
        f"| DD% | {ensemble_dd:.4f}% | {best_single['dd_pct']:.4f}% |\n",
        f"| Calmar | {ensemble_calmar:.4f} | {best_single['calmar']:.4f} |\n",
        f"| Trades | {ens_res['n_trades']} | {best_single['trades']} |\n",
        f"\n## Улучшение\n",
        f"- Return: {ret_diff:+.4f}%\n",
        f"- DD снижение: {dd_improvement:.1f}%\n",
    ]
    with open(report_path, 'w') as f:
        f.writelines(lines)
    print(f"\nReport saved: {report_path}")

if __name__ == '__main__':
    main()
