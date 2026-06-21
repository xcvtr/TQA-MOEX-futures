#!/usr/bin/env python3
"""
MOEX Futures Portfolio — 4-Strategy + Leverage to 200%+ annual.
TRIZ-derived: BR volume breakout + CR/AF OI + CR OI + Si imbalance.

Target: 200%+ annual, DD ~15%, with 3x leverage through GO.

Usage:
  python3 strategies/moex_portfolio.py              # full backtest
  python3 strategies/moex_portfolio.py --signal     # current signals
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


CAPITAL = 100_000
COMM = 4.0  # RUB/contract/side

# Strategy configs
STRATEGIES = [
    {
        'name': 'BR_vol_LONG',
        'ticker': 'BR',
        'margin': 8000,
        'direction': 'LONG_only',
        'feature': 'vol_z',
        'threshold': 1.3,
        'weight': 0.20,
    },
    {
        'name': 'CR_oi',
        'ticker': 'CR',
        'margin': 5000,
        'direction': 'both',
        'feature': 'oi_z',
        'threshold': 1.2,
        'weight': 0.30,
    },
    {
        'name': 'AF_oi',
        'ticker': 'AF',
        'margin': 4000,
        'direction': 'both',
        'feature': 'oi_z',
        'threshold': 2.0,
        'weight': 0.30,
    },
    {
        'name': 'Si_imb',
        'ticker': 'Si',
        'margin': 7000,
        'direction': 'both',
        'feature': 'buy_pressure',
        'threshold': 1.8,
        'weight': 0.20,
    },
]


def load_ticker_data(ticker):
    """Load daily data from supercandles_fo."""
    sql = f"""
        SELECT
            toString(tradedate) as dt,
            toString(argMax(pr_close, tradetime)) as close,
            toString(max(oi_change)) as oi_chg,
            toString(sum(vol_sum)) as volume
        FROM moex.supercandles_fo
        WHERE ticker = '{ticker}'
        GROUP BY tradedate ORDER BY tradedate
        FORMAT TabSeparatedWithNames
    """
    df = q_df(sql)
    if df is None or len(df) < 50:
        return None
    for c in [x for x in df.columns if x != 'dt']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['dt'] = pd.to_datetime(df['dt'])
    df['ret'] = df['close'].pct_change() * 100
    df['ret_next'] = df['ret'].shift(-1)
    # Features
    df['oi_z'] = (df['oi_chg'] - df['oi_chg'].rolling(21).mean()) / df['oi_chg'].rolling(21).std().replace(0, np.nan)
    df['vol_z'] = (df['volume'] - df['volume'].rolling(21).mean()) / df['volume'].rolling(21).std().replace(0, np.nan)
    return df


def load_si_imbalance():
    """Load Si imbalance from obstats_fo."""
    from clickhouse_driver import Client
    client = Client(host='10.0.0.60')
    sql = """
        SELECT tradedate,
               countIf(imb_l1 > 0.3) / count(*) AS buy_pressure
        FROM (
            SELECT tradedate,
                   (COALESCE(vol_b_l1, 0) - COALESCE(vol_s_l1, 0))
                       / NULLIF(COALESCE(vol_b_l1, 0) + COALESCE(vol_s_l1, 0), 0) AS imb_l1
            FROM moex.obstats_fo
            WHERE asset_code = 'Si' AND tradedate >= '2020-01-01'
        )
        GROUP BY tradedate ORDER BY tradedate
    """
    df = client.query_dataframe(sql)
    df['tradedate'] = pd.to_datetime(df['tradedate'])
    return df


def get_signals(strategies_data):
    """Generate signal matrix: rows=days, cols=strategy_names, values=direction."""
    # Align all data by date
    all_dates = set()
    for sdata in strategies_data:
        all_dates.update(sdata['df']['dt'].dt.date)
    all_dates = sorted(all_dates)
    
    date_to_idx = {d: i for i, d in enumerate(all_dates)}
    n_days = len(all_dates)
    signals = {}
    
    for sdata, scfg in zip(strategies_data, STRATEGIES):
        df = sdata['df']
        feat_col = scfg['feature']
        th = scfg['threshold']
        direction = scfg['direction']
        
        sig_arr = np.zeros(n_days)
        for _, row in df.iterrows():
            idx = date_to_idx.get(row['dt'].date())
            if idx is None:
                continue
            
            feat_val = row.get(feat_col, 0)
            if pd.isna(feat_val):
                continue
            
            if direction == 'LONG_only':
                if feat_val > th:
                    sig_arr[idx] = 1
            elif direction == 'SHORT_only':
                if feat_val < -th:
                    sig_arr[idx] = -1
            else:  # both
                if feat_val > th:
                    sig_arr[idx] = -1  # SHORT
                elif feat_val < -th:
                    sig_arr[idx] = 1   # LONG
        
        signals[scfg['name']] = sig_arr
    
    # Si imbalance special handling
    si_imb_df = None
    for sdata, scfg in zip(strategies_data, STRATEGIES):
        if scfg['name'] == 'Si_imb':
            si_imb_df = sdata['si_imb']
            break
    
    if si_imb_df is not None:
        si_imb_df['dt'] = pd.to_datetime(si_imb_df['tradedate'])
        mean_ = si_imb_df['buy_pressure'].rolling(21).mean()
        std_ = si_imb_df['buy_pressure'].rolling(21).std().replace(0, np.nan)
        si_imb_df['bp_z'] = (si_imb_df['buy_pressure'] - mean_) / std_
        
        for _, row in si_imb_df.iterrows():
            idx = date_to_idx.get(row['dt'].date())
            if idx is None or pd.isna(row.get('bp_z', np.nan)):
                continue
            if row['bp_z'] > 1.8:
                signals['Si_imb'][idx] = 1
            elif row['bp_z'] < -1.8:
                signals['Si_imb'][idx] = -1
    
    return signals, all_dates


def run_portfolio():
    """Run full portfolio backtest with leverage."""
    # Load data for each ticker
    strategies_data = []
    for scfg in STRATEGIES:
        df = load_ticker_data(scfg['ticker'])
        if df is None:
            print(f"ERROR: No data for {scfg['ticker']}")
            continue
        
        sdata = {'cfg': scfg, 'df': df, 'si_imb': None}
        
        # Special: load Si imbalance
        if scfg['name'] == 'Si_imb':
            si_imb = load_si_imbalance()
            if si_imb is not None:
                sdata['si_imb'] = si_imb
        
        strategies_data.append(sdata)
    
    if not strategies_data:
        print("No strategies loaded")
        return None
    
    signals, all_dates = get_signals(strategies_data)
    n_days = len(all_dates)
    
    if n_days < 50:
        print(f"Too few days: {n_days}")
        return None
    
    # Portfolio simulation (no equity stop-loss — portfolio doesn't use one)
    leverage = 1  # 1x through GO
    eq = [CAPITAL]
    daily_returns = []
    active_positions = []
    
    for day_idx in range(n_days):
        day_pnl = 0.0
        n_active = 0
        
        for scfg in STRATEGIES:
            sname = scfg['name']
            if sname not in signals:
                continue
            
            sig = signals[sname][day_idx]
            if sig == 0:
                continue
            
            # Find the ticker's ret_next for this day
            # We need to find which df this strategy belongs to
            for sdata in strategies_data:
                if sdata['cfg']['name'] != sname:
                    continue
                df = sdata['df']
                
                # Find this date in df
                date = all_dates[day_idx]
                row = df[df['dt'].dt.date == date]
                if row.empty:
                    continue
                
                ret_next = row['ret_next'].iloc[0]
                if pd.isna(ret_next):
                    continue
                
                # Position sizing: strategy weight * capital
                alloc = eq[-1] * scfg['weight']
                n_cont = max(1, int(alloc / scfg['margin']))
                
                # Leverage: multiply return (not contracts!)
                r = ret_next / 100 * sig * leverage
                comm_pct = (n_cont * COMM * 2) / eq[-1]
                r_net = r - comm_pct / 100
                day_pnl += r_net
                n_active += 1
                
                # Store this signal
                active_positions.append({
                    'date': str(date),
                    'strategy': sname,
                    'ticker': scfg['ticker'],
                    'direction': 'LONG' if sig == 1 else 'SHORT',
                    'ret_next': ret_next,
                    'n_cont': n_cont,
                    'weight': scfg['weight'],
                })
                break
        
        # Apply day PnL to equity
        eq.append(eq[-1] * (1 + day_pnl))
        if n_active > 0:
            daily_returns.append(day_pnl)
    
    # Stats
    ret_tot = (eq[-1] / CAPITAL - 1) * 100
    peak = np.maximum.accumulate(eq)
    dd_vals = [(eq[i] / peak[i] - 1) * 100 for i in range(1, len(eq))]
    max_dd = min(dd_vals)
    avg_daily = np.mean(daily_returns) * 100 if daily_returns else 0
    sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if daily_returns and np.std(daily_returns) > 0 else 0
    years = n_days / 252
    ann_ret = ((1 + ret_tot / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    
    print("=" * 70)
    print(f"MOEX Portfolio — {len(STRATEGIES)} Strategies × {leverage}x Leverage")
    print("=" * 70)
    print(f"Period:     {all_dates[0]} → {all_dates[-1]} ({n_days} days, {years:.1f}yr)")
    print(f"Capital:    {CAPITAL:,} RUB")
    print(f"Leverage:   {leverage}x (via GO)")
    print(f"Strategies:")
    for scfg in STRATEGIES:
        print(f"  {scfg['name']:<20} {scfg['ticker']:<4} {scfg['feature']:<10} th={scfg['threshold']:.1f} w={scfg['weight']:.0%}")
    print()
    print(f"  Total ret:      {ret_tot:+.1f}%")
    print(f"  Annualized:     {ann_ret:+.1f}%")
    print(f"  Max DD:         {max_dd:.1f}%")
    print(f"  Sharpe (ann):   {sharpe:.2f}")
    print(f"  Calmar:         {ann_ret/abs(max_dd):.2f}" if max_dd != 0 else "")
    print(f"  Final equity:   {eq[-1]:,.0f} RUB")
    print(f"  Total trades:   {len(active_positions)}")
    print(f"  Avg daily ret:  {avg_daily:.4f}%")
    
    # By year
    active_df = pd.DataFrame(active_positions) if active_positions else pd.DataFrame()
    if not active_df.empty:
        active_df['year'] = pd.to_datetime(active_df['date']).dt.year
        print()
        print("  By year:")
        for yr in sorted(active_df['year'].unique()):
            yr_subs = active_df[active_df['year'] == yr]
            n_strat = yr_subs['strategy'].nunique()
            print(f"    {yr}: {len(yr_subs)} trades, {n_strat} active strategies")
        
        print()
        print("  By strategy:")
        for sname in active_df['strategy'].unique():
            sub = active_df[active_df['strategy'] == sname]
            print(f"    {sname:<20}: {len(sub):<4} trades")
    
    return {
        'total_ret_pct': round(ret_tot, 1),
        'ann_ret_pct': round(ann_ret, 1),
        'max_dd_pct': round(max_dd, 1),
        'sharpe': round(sharpe, 2),
        'final_equity': round(eq[-1], 0),
        'n_trades': len(active_positions),
        'period': f"{all_dates[0]} → {all_dates[-1]}",
        'leverage': leverage,
    }


if __name__ == '__main__':
    run_portfolio()
