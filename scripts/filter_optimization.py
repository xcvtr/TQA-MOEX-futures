#!/usr/bin/env python3
"""
Direction 2: Entry Filters for BR SMA Mean Reversion.
Tests Volatility (ATR), ADX, and Volume filters against baseline.
(Macro filter skipped — no RU economic data in DB.)
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

def compute_atr(df, period=14):
    hi, lo, cl = df['high'].values, df['low'].values, df['close'].values
    tr = np.zeros(len(df))
    tr[0] = hi[0] - lo[0]
    for i in range(1, len(df)):
        tr[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1]))
    atr = pd.Series(tr).rolling(period).mean().bfill().fillna(tr.mean())
    return atr.values

def compute_adx(df, period=14):
    hi, lo, cl = df['high'].values, df['low'].values, df['close'].values
    n = len(df)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = hi[i] - hi[i-1]
        down = lo[i-1] - lo[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down
    tr = np.zeros(n)
    tr[0] = hi[0] - lo[0]
    for i in range(1, n):
        tr[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1]))
    atr_series = pd.Series(tr).rolling(period).mean().bfill().fillna(tr.mean()).values
    plus_di = 100 * pd.Series(plus_dm).rolling(period).mean().fillna(0).values / atr_series
    minus_di = 100 * pd.Series(minus_dm).rolling(period).mean().fillna(0).values / atr_series
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = pd.Series(dx).rolling(period).mean().bfill().fillna(25).values
    return adx

def generate_signals_sma_with_filters(daily_ohlcv, sma_fast=5, sma_slow=20,
                                       threshold=0.001, hold_days=10,
                                       use_volatility_filter=False,
                                       use_adx_filter=False,
                                       use_volume_filter=False,
                                       atr_mult=1.5, adx_threshold=20,
                                       vol_sma_period=20, vol_mult=0.8):
    df = daily_ohlcv[['open', 'high', 'low', 'close', 'volume']].copy()
    df['sma_fast'] = df['close'].rolling(sma_fast).mean()
    df['sma_slow'] = df['close'].rolling(sma_slow).mean()
    df['basis'] = (df['sma_fast'] - df['sma_slow']) / df['sma_slow']
    df['basis'] = df['basis'].fillna(0)

    if use_volatility_filter:
        atr_vals = compute_atr(daily_ohlcv, 14)
        median_atr = np.median(atr_vals)
        df['vol_filter'] = atr_vals <= median_atr * atr_mult
    else:
        df['vol_filter'] = True

    if use_adx_filter:
        adx_vals = compute_adx(daily_ohlcv, 14)
        df['adx_filter'] = adx_vals >= adx_threshold
    else:
        df['adx_filter'] = True

    if use_volume_filter:
        df['vol_sma'] = df['volume'].rolling(vol_sma_period).mean()
        df['vol_filter2'] = df['volume'] >= df['vol_sma'] * vol_mult
    else:
        df['vol_filter2'] = True

    df['signal'] = 0
    in_pos = False
    entry_i = 0
    for i in range(len(df)):
        if not in_pos:
            cond = (df['basis'].iloc[i] < -threshold
                    and df['vol_filter'].iloc[i]
                    and df['adx_filter'].iloc[i]
                    and df['vol_filter2'].iloc[i])
            if cond:
                df.iloc[i, df.columns.get_loc('signal')] = 1
                in_pos = True
                entry_i = i
        else:
            if (i - entry_i) >= hold_days or df['basis'].iloc[i] > 0:
                in_pos = False
    return df

def run_sim(daily_ohlcv, sig_df, margin_usage=0.10, stop_loss_pct=0.05, max_hold_days=10):
    pf = DailyPortfolio(margin_usage=margin_usage, stop_loss_pct=stop_loss_pct,
                        max_hold_days=max_hold_days, initial_capital=100000.0)
    return pf.run(daily_ohlcv, sig_df[['signal']])

def walkforward_filter(daily_ohlcv, filter_config, param_grid):
    n = len(daily_ohlcv)
    fs = n // 4
    folds = [(0, fs), (fs, 2*fs), (2*fs, 3*fs), (3*fs, n)]
    results = []
    for mu, hold, sl in param_grid:
        frs = []
        for fi, (f0, f1) in enumerate(folds):
            fd = daily_ohlcv.iloc[f0:f1].copy()
            if len(fd) < 5:
                frs.append({'fold': fi+1, 'return_pct': 0.0, 'max_dd_pct': 0.0, 'calmar': 0.0, 'n_signals': 0, 'trades': 0})
                continue
            sd = generate_signals_sma_with_filters(fd, hold_days=hold, **filter_config)
            sd['signal'] = sd['signal'].clip(0, 1)
            ns = int(sd['signal'].sum())
            if ns < 1:
                frs.append({'fold': fi+1, 'return_pct': 0.0, 'max_dd_pct': 0.0, 'calmar': 0.0, 'n_signals': 0, 'trades': 0})
                continue
            r = run_sim(fd, sd, mu, sl, hold)
            frs.append({'fold': fi+1, 'return_pct': r['total_return_pct'], 'max_dd_pct': r['max_dd_pct'],
                        'calmar': r['calmar'], 'n_signals': ns, 'trades': len(r['trades'])})
        ap = all(fr['return_pct'] > 0 for fr in frs if fr['n_signals'] > 0)
        results.append({'params': {'mu': mu, 'hold': hold, 'sl': sl}, 'fold_results': frs, 'all_profitable': ap})
    return results

def main():
    print("=" * 70)
    print("НАПРАВЛЕНИЕ 2: ФИЛЬТРЫ ВХОДА")
    print("=" * 70)

    daily = fetch_daily_br()
    print(f"\nData: {len(daily)} daily bars, {daily.index[0].date()} → {daily.index[-1].date()}")

    param_grid = list(product([0.10, 0.20, 0.50], [5, 10, 21], [0.03, 0.05, 0.10]))

    # Baseline
    print(f"\n{'─' * 70}")
    print("БАЗОВАЯ СТРАТЕГИЯ (SMA5 < SMA20, без фильтров)")
    base_best = None
    base_best_calmar = -999
    for mu, hold, sl in param_grid:
        sig = generate_signals_sma_with_filters(daily, hold_days=hold)
        sig['signal'] = sig['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], sig, mu, sl, hold)
        if res['calmar'] > base_best_calmar:
            base_best_calmar = res['calmar']
            base_best = {'mu': mu, 'hold': hold, 'sl': sl, 'res': res}
    print(f"Best: mu={base_best['mu']} hold={base_best['hold']} sl={base_best['sl']}  "
          f"ret={base_best['res']['total_return_pct']:+.4f}%  DD={base_best['res']['max_dd_pct']:.4f}%  "
          f"Calmar={base_best['res']['calmar']:.4f}  trades={base_best['res']['n_trades']}")

    # Individual filters
    filters = {
        'Volatility (ATR<1.5*median)': {'use_volatility_filter': True},
        'ADX (>20)': {'use_adx_filter': True},
        'Volume (>0.8*SMA20)': {'use_volume_filter': True},
    }
    all_filters = {
        'Vol+ADX+Volume': {'use_volatility_filter': True, 'use_adx_filter': True, 'use_volume_filter': True},
    }

    print(f"\n{'─' * 70}")
    print("ОТДЕЛЬНЫЕ ФИЛЬТРЫ (на лучших параметрах базы)")
    filter_results = []
    for fname, fconfig in filters.items():
        sig = generate_signals_sma_with_filters(daily, hold_days=base_best['hold'], **fconfig)
        sig['signal'] = sig['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], sig,
                      base_best['mu'], base_best['sl'], base_best['hold'])
        wr = sum(1 for t in res['trades'] if t['pnl'] > 0) / res['n_trades'] * 100 if res['n_trades'] > 0 else 0
        filter_results.append({
            'filter': fname,
            'return_pct': res['total_return_pct'],
            'dd_pct': res['max_dd_pct'],
            'calmar': res['calmar'],
            'trades': res['n_trades'],
            'wr': wr,
        })
        print(f"  {fname:30s}  ret={res['total_return_pct']:+8.4f}%  DD={res['max_dd_pct']:.4f}%  "
              f"Calmar={res['calmar']:.4f}  trades={res['n_trades']}  WR={wr:.1f}%")

    # Combined filter
    print(f"\n{'─' * 70}")
    print("КОМБИНАЦИЯ ВСЕХ ФИЛЬТРОВ")
    for fname, fconfig in all_filters.items():
        sig = generate_signals_sma_with_filters(daily, hold_days=base_best['hold'], **fconfig)
        sig['signal'] = sig['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], sig,
                      base_best['mu'], base_best['sl'], base_best['hold'])
        wr = sum(1 for t in res['trades'] if t['pnl'] > 0) / res['n_trades'] * 100 if res['n_trades'] > 0 else 0
        filter_results.append({
            'filter': fname,
            'return_pct': res['total_return_pct'],
            'dd_pct': res['max_dd_pct'],
            'calmar': res['calmar'],
            'trades': res['n_trades'],
            'wr': wr,
        })
        print(f"  {fname:30s}  ret={res['total_return_pct']:+8.4f}%  DD={res['max_dd_pct']:.4f}%  "
              f"Calmar={res['calmar']:.4f}  trades={res['n_trades']}  WR={wr:.1f}%")

    # Compare with base
    print(f"\n{'─' * 70}")
    print("СРАВНЕНИЕ С БАЗОЙ")
    print(f"{'Фильтр':30s}  {'Return%':>10s}  {'DD%':>8s}  {'Calmar':>8s}  {'Trades':>7s}  {'WR%':>7s}")
    print(f"{'─' * 80}")
    print(f"{'BASELINE':30s}  {base_best['res']['total_return_pct']:>+8.4f}%  "
          f"{base_best['res']['max_dd_pct']:>7.4f}%  {base_best['res']['calmar']:>8.4f}  "
          f"{base_best['res']['n_trades']:>7d}  "
          f"{sum(1 for t in base_best['res']['trades'] if t['pnl'] > 0) / max(base_best['res']['n_trades'], 1) * 100:>6.1f}%")
    for fr in filter_results:
        print(f"{fr['filter']:30s}  {fr['return_pct']:>+8.4f}%  {fr['dd_pct']:>7.4f}%  "
              f"{fr['calmar']:>8.4f}  {fr['trades']:>7d}  {fr['wr']:>6.1f}%")

    # Walk-forward on best filter combination
    print(f"\n{'─' * 70}")
    print("WALK-FORWARD: БАЗА vs ВСЕ ФИЛЬТРЫ")
    wf_results = {}
    for label, fconfig in [('BASELINE', {}), ('Vol+ADX+Volume', {'use_volatility_filter': True, 'use_adx_filter': True, 'use_volume_filter': True})]:
        wf = walkforward_filter(daily, fconfig, param_grid)
        wf_results[label] = wf
        pc = [r for r in wf if r['all_profitable']]
        print(f"\n  {label}: {len(pc)}/{len(wf)} combos profitable in all folds")
        for r in wf[:3]:
            rets = [f'{f["return_pct"]:+.2f}%' for f in r['fold_results']]
            m = '✓' if r['all_profitable'] else '✗'
            print(f"    {m} mu={r['params']['mu']:.2f} hold={int(r['params']['hold']):2d} sl={r['params']['sl']:.2f}  {rets}")

    # Save report
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(report_dir, f'{date_str}-filter-optimization.md')
    lines = [
        f"# Отчёт: Оптимизация фильтров входа для BR SMA Mean Reversion\n",
        f"**Дата:** {date_str}\n",
        f"**Данные:** {len(daily)} daily баров ({daily.index[0].date()} → {daily.index[-1].date()})\n",
        f"\n## Базовая стратегия\n",
        f"Параметры: mu={base_best['mu']}, hold={base_best['hold']}, sl={base_best['sl']}\n",
        f"Return: {base_best['res']['total_return_pct']:+.4f}%, "
        f"DD: {base_best['res']['max_dd_pct']:.4f}%, "
        f"Calmar: {base_best['res']['calmar']:.4f}, "
        f"Trades: {base_best['res']['n_trades']}\n",
        f"\n## Результаты фильтров\n",
        f"\n| Фильтр | Return% | DD% | Calmar | Trades | WR% |\n",
        f"|---|---|---|---|---|---|\n",
    ]
    wr_base = sum(1 for t in base_best['res']['trades'] if t['pnl'] > 0) / max(base_best['res']['n_trades'], 1) * 100
    lines.append(f"| BASELINE | {base_best['res']['total_return_pct']:+.4f}% | {base_best['res']['max_dd_pct']:.4f}% | {base_best['res']['calmar']:.4f} | {base_best['res']['n_trades']} | {wr_base:.1f}% |\n")
    for fr in filter_results:
        lines.append(f"| {fr['filter']} | {fr['return_pct']:+.4f}% | {fr['dd_pct']:.4f}% | {fr['calmar']:.4f} | {fr['trades']} | {fr['wr']:.1f}% |\n")
    lines.append(f"\n## Walk-forward\n")
    baseline_ok = len([r for r in wf_results['BASELINE'] if r['all_profitable']])
    lines.append(f"- BASELINE: {baseline_ok} profitable combos\n")
    filtered_ok = len([r for r in wf_results['Vol+ADX+Volume'] if r['all_profitable']])
    lines.append(f"- С фильтрами: {filtered_ok} profitable combos\n")
    with open(report_path, 'w') as f:
        f.writelines(lines)
    print(f"\nReport saved: {report_path}")

    return filter_results

if __name__ == '__main__':
    main()
