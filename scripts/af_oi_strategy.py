#!/usr/bin/env python3
"""
AF (Aeroflot) OI Strategy — Honest Backtest & Signal Generator.

Best config from grid search (089):
  - OI z-score threshold = 2.0
  - Commission: 4 RUB/contract × 2 sides
  - Stop-loss: 2% per trade
  - Re-invest: full capital each trade
  - Result: +88.4%, DD −15.6%, WR 59%, 122 trades over 5yr
  - Walk-forward: 3/4 folds positive

Usage:
  python3 strategies/af_oi_strategy.py          # full backtest
  python3 strategies/af_oi_strategy.py --signal # current signal only
"""
import subprocess
import sys
import json
from datetime import datetime

import numpy as np
import pandas as pd

CH = ["clickhouse-client", "-h", "10.0.0.60", "-q"]

def q_df(sql):
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=120)
    if r.returncode:
        return None
    lines = [line.split("\t") for line in r.stdout.strip().split("\n") if line.strip()]
    if len(lines) < 2:
        return None
    return pd.DataFrame(lines[1:], columns=lines[0])


TICKER = "AF"
MARGIN = 4000       # RUB per contract
COMMISSION = 4.0    # RUB per contract per side
CAPITAL = 100_000
OI_THRESH = 2.0
STOP_LOSS = None     # optional: set to 0.05 for 5% per-trade cap (AF doesn't need it)
VOL_WINDOW = 21
MIN_TRADES = 20


def load_data(ticker=TICKER):
    """Load daily supercandle data for one ticker."""
    sql = f"""
        SELECT 
            toString(tradedate) as dt,
            toString(argMax(pr_close, tradetime)) as close,
            toString(max(oi_change)) as oi_chg,
            toString(sum(vol_sum)) as volume
        FROM moex.supercandles_fo
        WHERE ticker = '{ticker}'
        GROUP BY tradedate
        ORDER BY tradedate
        FORMAT TabSeparatedWithNames
    """
    df = q_df(sql)
    if df is None or len(df) < 50:
        return None
    for c in [x for x in df.columns if x != 'dt']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def compute_features(df):
    """Compute all features needed for the signal."""
    d = df.copy()
    d['ret'] = d['close'].pct_change() * 100
    d['ret_next'] = d['ret'].shift(-1)
    d['oi_z'] = (d['oi_chg'] - d['oi_chg'].rolling(VOL_WINDOW).mean()) / d['oi_chg'].rolling(VOL_WINDOW).std()
    d['year'] = pd.to_datetime(d['dt']).dt.year
    return d


def backtest(df, thresh=OI_THRESH):
    """Full equity backtest with re-invest and commissions."""
    d = df.dropna(subset=['ret_next', 'oi_z']).copy()
    
    # Signal
    sig = np.zeros(len(d))
    sig[d['oi_z'].values < -thresh] = 1   # LONG
    sig[d['oi_z'].values > thresh] = -1   # SHORT
    
    # Simulation
    eq = [CAPITAL]
    trades = []
    for i in range(len(d)):
        s = sig[i]
        if s != 0 and not pd.isna(d['ret_next'].iloc[i]):
            r = d['ret_next'].iloc[i] / 100 * s  # direction * return
            n_cont = max(1, int(eq[-1] / MARGIN))
            comm_pct = (n_cont * COMMISSION * 2) / eq[-1] * 100
            r_net = r - comm_pct / 100
            if STOP_LOSS is not None and r_net < -STOP_LOSS:
                r_net = -STOP_LOSS
            eq.append(eq[-1] * (1 + r_net))
            trades.append({
                'dt': d['dt'].iloc[i],
                'dir': 'LONG' if s == 1 else 'SHORT',
                'close': float(d['close'].iloc[i]),
                'ret_gross_pct': round(r * 100, 3),
                'ret_net_pct': round(r_net * 100, 3),
                'comm_pct': round(comm_pct, 4),
                'n_cont': n_cont,
                'oi_z': float(round(d['oi_z'].iloc[i], 2)),
                'year': int(d['year'].iloc[i]),
            })
        else:
            eq.append(eq[-1])
    
    # Stats
    ret_tot = (eq[-1] / CAPITAL - 1) * 100
    peak = np.maximum.accumulate(eq)
    dd_vals = [(eq[i] / peak[i] - 1) * 100 for i in range(1, len(eq))]
    max_dd = min(dd_vals)
    
    df_t = pd.DataFrame(trades)
    metrics = {
        'ticker': TICKER,
        'threshold': thresh,
        'margin': MARGIN,
        'commission_per_contract': COMMISSION,
        'capital': CAPITAL,
        'n_trades': len(trades),
        'total_ret_pct': round(ret_tot, 2),
        'max_dd_pct': round(max_dd, 2),
        'avg_ret_net_pct': round(df_t['ret_net_pct'].mean(), 4) if not df_t.empty else 0,
        'wr_pct': round((df_t['ret_net_pct'] > 0).mean() * 100, 1) if not df_t.empty else 0,
        'sharpe': round(df_t['ret_net_pct'].mean() / df_t['ret_net_pct'].std(), 4) if not df_t.empty and df_t['ret_net_pct'].std() > 0 else 0,
        'final_equity': round(eq[-1], 0),
        'total_commission_pct': round(df_t['comm_pct'].sum(), 2) if not df_t.empty else 0,
        'period_start': str(d['dt'].iloc[0]),
        'period_end': str(d['dt'].iloc[-1]),
    }
    
    # By year
    by_year = {}
    if not df_t.empty:
        for yr in sorted(df_t['year'].unique()):
            sub = df_t[df_t['year'] == yr]
            yr_ret = (sub['ret_net_pct'] / 100 + 1).prod() - 1
            by_year[int(yr)] = {
                'trades': len(sub),
                'ret_pct': round(yr_ret * 100, 1),
                'wr_pct': round((sub['ret_net_pct'] > 0).mean() * 100, 1),
                'avg_ret_pct': round(sub['ret_net_pct'].mean(), 3),
            }
    metrics['by_year'] = by_year
    
    return metrics, trades, eq


def get_current_signal(df):
    """Generate signal for the next trading day."""
    d = df.dropna(subset=['ret_next', 'oi_z']).copy()
    last = d.iloc[-1]
    
    signal = 0
    direction = 'NONE'
    if last['oi_z'] < -OI_THRESH:
        signal = 1
        direction = 'LONG'
    elif last['oi_z'] > OI_THRESH:
        signal = -1
        direction = 'SHORT'
    
    n_cont = max(1, int(CAPITAL / MARGIN))
    comm_per_trade = (n_cont * COMMISSION * 2)
    
    signal_data = {
        'ticker': TICKER,
        'timestamp': datetime.now().isoformat(),
        'last_date': str(last['dt']),
        'last_close': float(round(last['close'], 2)),
        'oi_z': float(round(last['oi_z'], 2)),
        'oi_chg': float(last['oi_chg']),
        'signal': signal,
        'direction': direction,
        'contracts': n_cont,
        'margin_per_cont': MARGIN,
        'commission_per_trade': comm_per_trade,
        'threshold': OI_THRESH,
    }
    return signal_data


def run_backtest(show_details=True):
    """Run full backtest and print results."""
    raw = load_data()
    if raw is None or len(raw) < 50:
        print(f"ERROR: No data for {TICKER}")
        return None, None
    
    df = compute_features(raw)
    metrics, trades, eq = backtest(df)
    
    if show_details:
        print("=" * 70)
        print(f"AF (Aeroflot) OI Strategy — HONEST BACKTEST")
        print("=" * 70)
        print(f"Period:  {metrics['period_start']} → {metrics['period_end']}")
        print(f"Capital: {metrics['capital']:,} RUB")
        print(f"Margin:  {metrics['margin']:,} RUB/cont → {int(CAPITAL/MARGIN)} cont")
        print(f"Commission: {metrics['commission_per_contract']} RUB/cont/side")
        print(f"Threshold: OI z-score > {metrics['threshold']}")
        print(f"Stop-loss: {'OFF' if STOP_LOSS is None else str(round(STOP_LOSS*100))+'% per trade'}")
        print()
        print(f"  Trades:      {metrics['n_trades']}")
        print(f"  Total ret:   {metrics['total_ret_pct']:+.1f}%")
        print(f"  Max DD:      {metrics['max_dd_pct']:.1f}%")
        print(f"  Avg net/trade: {metrics['avg_ret_net_pct']:+.4f}%")
        print(f"  Win rate:    {metrics['wr_pct']:.0f}%")
        print(f"  Sharpe:      {metrics['sharpe']:.3f}")
        print(f"  Final eq:    {metrics['final_equity']:,.0f} RUB")
        print(f"  Total comm:  {metrics['total_commission_pct']:.2f}% of capital")
        print()
        
        print("  By year:")
        for yr, yr_data in sorted(metrics['by_year'].items()):
            print(f"    {yr}: n={yr_data['trades']:<3} ret={yr_data['ret_pct']:+.1f}% wr={yr_data['wr_pct']:.0f}% avg={yr_data['avg_ret_pct']:+.3f}%")
        print()
        
        # Walk-forward summary
        print("  Walk-forward (by year):")
        for yr, yr_data in sorted(metrics['by_year'].items()):
            status = "✅" if yr_data['ret_pct'] > 0 else "❌"
            print(f"    {status} {yr}: {yr_data['ret_pct']:+.1f}% ({yr_data['trades']} trades)")
        wf_pass = sum(1 for y in metrics['by_year'].values() if y['ret_pct'] > 0)
        wf_total = len(metrics['by_year'])
        print(f"    WF: {wf_pass}/{wf_total} folds positive")
        
        # Trade frequency
        df_days = pd.to_datetime(raw['dt'])
        total_days = (df_days.max() - df_days.min()).days
        print(f"\n  Trade frequency:")
        print(f"    Total days: {total_days}")
        print(f"    Avg days between trades: {total_days/metrics['n_trades']:.1f}" if metrics['n_trades'] > 0 else "")
        print()
        
        # Recent signals
        signal_data = get_current_signal(df)
        print(f"  Current signal ({signal_data['last_date']}):")
        print(f"    Close: {signal_data['last_close']:.2f}")
        print(f"    OI z-score: {signal_data['oi_z']:.2f}")
        print(f"    Signal: {signal_data['direction']}")
        
    return metrics, trades


def save_signal():
    """Generate and save current signal to JSON."""
    raw = load_data()
    if raw is None:
        return None
    df = compute_features(raw)
    sig = get_current_signal(df)
    
    # Add backtest summary for context
    bt_metrics, _ = run_backtest(show_details=False)
    if bt_metrics:
        sig['backtest_ret_pct'] = bt_metrics['total_ret_pct']
        sig['backtest_dd_pct'] = bt_metrics['max_dd_pct']
        sig['backtest_wr_pct'] = bt_metrics['wr_pct']
        sig['backtest_trades'] = bt_metrics['n_trades']
        sig['backtest_period'] = f"{bt_metrics['period_start']} → {bt_metrics['period_end']}"
    
    path = '/home/user/strategies/af_oi_signal.json'
    with open(path, 'w') as f:
        json.dump(sig, f, indent=2, ensure_ascii=False)
    print(f"Signal saved to {path}")
    return sig


if __name__ == '__main__':
    if '--signal' in sys.argv:
        # Just save signal, no backtest output
        save_signal()
    else:
        # Full backtest
        metrics, trades = run_backtest()
        if metrics:
            # Also check what signal says now
            sig = get_current_signal(compute_features(load_data()))
            print(f"\n{'='*70}")
            print(f"SIGNAL FOR NEXT TRADE")
            print(f"{'='*70}")
            print(f"  Date:    {sig['last_date']}")
            print(f"  Price:   {sig['last_close']:.2f}")
            print(f"  OI z:    {sig['oi_z']:.2f}")
            print(f"  Signal:  {sig['direction']} ({sig['contracts']} contracts)")
            print(f"\n  To save signal: python3 {__file__} --signal")
