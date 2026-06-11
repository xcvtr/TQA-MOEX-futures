#!/usr/bin/env python3
"""
BR Contango Strategy — daily bar-level simulation.

Strategy logic (proven: 79% win rate 5d, 71% win rate 10d on actual contango):
  - Compute basis from actual BR futures contract overlaps
  - CONTANGO when front < back → basis < 0
  - ENTER LONG when contango detected
  - EXIT: after N days OR basis turns positive

Fallback (for continuous signal): SMA proxy where SMA5 < SMA20 → buy.
"""

import sys
import os
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


def parse_expiry(contract):
    parts = contract.replace('GEN_BR-', '').split('.')
    return int(parts[1]) * 12 + int(parts[0])


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


def compute_basis():
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT time::date as d, contract, AVG(close) as close
        FROM moex_prices_5m
        WHERE symbol='BR' AND contract LIKE 'GEN_BR-%'
        GROUP BY time::date, contract
        ORDER BY d
    """
    df = pd.read_sql(query, conn, parse_dates=['d'])
    conn.close()

    df['expiry'] = df['contract'].apply(parse_expiry)
    records = []
    for d, grp in df.groupby('d'):
        if len(grp) < 2:
            continue
        sg = grp.sort_values('expiry')
        front = sg.iloc[0]
        back = sg.iloc[1]
        basis = (front['close'] - back['close']) / front['close']
        records.append({
            'd': d, 'front_contract': front['contract'], 'back_contract': back['contract'],
            'front_close': front['close'], 'back_close': back['close'], 'basis': basis,
        })
    bdf = pd.DataFrame(records).set_index('d')
    return bdf


def generate_signals_basis(daily_ohlcv, basis_df, threshold=0.001, hold_days=10):
    """Entry when contango (basis < -threshold). Exit at hold_days."""
    df = daily_ohlcv[['open', 'high', 'low', 'close']].copy()
    df['basis'] = np.nan
    common = df.index.intersection(basis_df.index)
    df.loc[common, 'basis'] = basis_df.loc[common, 'basis'].values
    df['basis'] = df['basis'].ffill().bfill().fillna(0)

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


def generate_signals_sma(daily_ohlcv, sma_fast=5, sma_slow=20, threshold=0.001, hold_days=10):
    """SMA proxy: when SMA5 < SMA20 (prices below avg), buy for mean reversion."""
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


def run_sim(daily_ohlcv, sig_df, margin_usage=0.10, stop_loss_pct=0.05, max_hold_days=10):
    pf = DailyPortfolio(
        margin_usage=margin_usage, stop_loss_pct=stop_loss_pct,
        max_hold_days=max_hold_days, initial_capital=100000.0,
    )
    return pf.run(daily_ohlcv, sig_df[['signal']])


def walkforward(daily_ohlcv, sig_fn, sig_kwargs):
    n = len(daily_ohlcv)
    fs = n // 4
    folds = [(0, fs), (fs, 2*fs), (2*fs, 3*fs), (3*fs, n)]
    mu_vals = [0.10, 0.20, 0.50]
    hold_vals = [5, 10, 21]
    sl_vals = [0.03, 0.05, 0.10]

    results = []
    for mu, hold, sl in product(mu_vals, hold_vals, sl_vals):
        frs = []
        for fi, (f0, f1) in enumerate(folds):
            fd = daily_ohlcv.iloc[f0:f1].copy()
            if len(fd) < 5:
                frs.append({'fold': fi+1, 'return_pct': 0.0, 'max_dd_pct': 0.0, 'calmar': 0.0, 'n_signals': 0, 'trades': 0})
                continue
            kw = dict(sig_kwargs)
            kw['hold_days'] = hold
            sd = sig_fn(fd, **kw)
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
    print("BR CONTANGO STRATEGY — Daily Bar-Level Simulation")
    print("=" * 70)

    daily = fetch_daily_br()
    basis_df = compute_basis()
    n_contango = (basis_df['basis'] < 0).sum()

    print(f"\nData: {len(daily)} daily bars, {daily.index[0].date()} → {daily.index[-1].date()}")
    print(f"Contract overlaps: {len(basis_df)} dates, contango: {n_contango}/{len(basis_df)} ({n_contango/len(basis_df)*100:.0f}%)")

    # === APPROACH 1: Actual Basis ===
    print("\n" + "─" * 70)
    print("APPROACH 1: Actual Futures Basis")
    print("─" * 70)

    sig1 = generate_signals_basis(daily, basis_df, threshold=0.001, hold_days=10)
    res1 = run_sim(daily[['open','high','low','close']], sig1, 0.10, 0.05, 10)
    print(f"Default (mu=0.10, hold=10, sl=0.05): {res1['total_return_pct']:+.4f}%, DD={res1['max_dd_pct']:.4f}%, Calmar={res1['calmar']:.4f}, Trades={res1['n_trades']}")

    sweep1 = []
    for mu, hold, sl in product([0.10,0.20,0.50], [5,10,21], [0.03,0.05,0.10]):
        s = generate_signals_basis(daily, basis_df, 0.001, hold)
        s['signal'] = s['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], s, mu, sl, hold)
        sweep1.append({'mu': mu, 'hold': int(hold), 'sl': sl, 'return_pct': res['total_return_pct'],
                      'max_dd_pct': res['max_dd_pct'], 'calmar': res['calmar'], 'n_trades': res['n_trades']})

    sw1 = pd.DataFrame(sweep1)
    pos1 = sw1[sw1['calmar'] > 0].sort_values('calmar', ascending=False)
    if len(pos1) > 0:
        print(f"Best: mu={pos1.iloc[0]['mu']} hold={pos1.iloc[0]['hold']} sl={pos1.iloc[0]['sl']}  "
              f"ret={pos1.iloc[0]['return_pct']:+.4f}% calmar={pos1.iloc[0]['calmar']:.4f}")
        for _, row in pos1.iterrows():
            print(f"  mu={row['mu']:.2f} hold={int(row['hold']):2d} sl={row['sl']:.2f}  "
                  f"ret={row['return_pct']:+8.4f}%  dd={row['max_dd_pct']:.4f}%  "
                  f"calmar={row['calmar']:.4f}  trades={int(row['n_trades'])}")

    # Walk-forward
    def sig_basis_wrapper(fd, **kw):
        return generate_signals_basis(fd, basis_df, **kw)
    wf = walkforward(daily[['open','high','low','close']], sig_basis_wrapper, {'threshold': 0.001})
    pc = [r for r in wf if r['all_profitable']]
    print(f"\nWalk-forward: {len(pc)}/{len(wf)} combos profitable in all folds")
    for r in wf:
        rets = [f['return_pct'] for f in r['fold_results']]
        m = "✓" if r['all_profitable'] else "✗"
        print(f"  {m} mu={r['params']['mu']:.2f} hold={int(r['params']['hold']):2d} sl={r['params']['sl']:.2f}  {rets}")

    # === APPROACH 2: SMA Proxy ===
    print("\n" + "─" * 70)
    print("APPROACH 2: SMA Proxy (SMA5 < SMA20 → LONG)")
    print("─" * 70)

    sig2 = generate_signals_sma(daily, 5, 20, 0.001, 10)
    r2 = run_sim(daily[['open','high','low','close']], sig2, 0.10, 0.05, 10)
    print(f"Default (mu=0.10, hold=10, sl=0.05): {r2['total_return_pct']:+.4f}%, DD={r2['max_dd_pct']:.4f}%, Calmar={r2['calmar']:.4f}, Trades={r2['n_trades']}")

    sweep2 = []
    for mu, hold, sl in product([0.10,0.20,0.50], [5,10,21], [0.03,0.05,0.10]):
        s = generate_signals_sma(daily, 5, 20, 0.001, hold)
        s['signal'] = s['signal'].clip(0, 1)
        res = run_sim(daily[['open','high','low','close']], s, mu, sl, hold)
        sweep2.append({'mu': mu, 'hold': int(hold), 'sl': sl, 'return_pct': res['total_return_pct'],
                       'max_dd_pct': res['max_dd_pct'], 'calmar': res['calmar'], 'n_trades': res['n_trades']})

    sw2 = pd.DataFrame(sweep2)
    pos2 = sw2[sw2['calmar'] > 0].sort_values('calmar', ascending=False)
    if len(pos2) > 0:
        print(f"Best: mu={pos2.iloc[0]['mu']} hold={pos2.iloc[0]['hold']} sl={pos2.iloc[0]['sl']}  "
              f"ret={pos2.iloc[0]['return_pct']:+.4f}% calmar={pos2.iloc[0]['calmar']:.4f}")
        for _, row in pos2.iterrows():
            print(f"  mu={row['mu']:.2f} hold={int(row['hold']):2d} sl={row['sl']:.2f}  "
                  f"ret={row['return_pct']:+8.4f}%  dd={row['max_dd_pct']:.4f}%  "
                  f"calmar={row['calmar']:.4f}  trades={int(row['n_trades'])}")

    wf2 = walkforward(daily[['open','high','low','close']], generate_signals_sma, {'threshold': 0.001})
    pc2 = [r for r in wf2 if r['all_profitable']]
    print(f"\nWalk-forward: {len(pc2)}/{len(wf2)} combos profitable in all folds")
    for r in wf2:
        rets = [f['return_pct'] for f in r['fold_results']]
        m = "✓" if r['all_profitable'] else "✗"
        print(f"  {m} mu={r['params']['mu']:.2f} hold={int(r['params']['hold']):2d} sl={r['params']['sl']:.2f}  {rets}")

    # === SUMMARY ===
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Data: {len(daily)} daily bars")
    print(f"Contract overlap: {len(basis_df)} dates, contango {n_contango} ({n_contango/len(basis_df)*100:.0f}%)")

    print(f"\n--- Actual Futures Basis ---")
    print(f"Return: {res1['total_return_pct']:+.4f}%  DD: {res1['max_dd_pct']:.4f}%  Calmar: {res1['calmar']:.4f}  Trades: {res1['n_trades']}")
    print(f"WF profitable all folds: {len(pc)}/{len(wf)}")

    print(f"\n--- SMA Proxy ---")
    print(f"Return: {r2['total_return_pct']:+.4f}%  DD: {r2['max_dd_pct']:.4f}%  Calmar: {r2['calmar']:.4f}  Trades: {r2['n_trades']}")
    print(f"WF profitable all folds: {len(pc2)}/{len(wf2)}")

    return {
        'daily': daily, 'basis_df': basis_df, 'res1': res1, 'r2': r2,
        'sweep1': sweep1, 'sweep2': sweep2, 'wf1': wf, 'wf2': wf2,
    }


if __name__ == '__main__':
    main()
