#!/usr/bin/env python3
"""
Si (USD/RUB) Volume Imbalance Strategy — Honest Backtest & Signal Generator.

Best config from volume imbalance scan:
  - Feature: buy_pressure (fraction of snapshots with buy imbalance > 0.3)
  - Threshold: 3.0 (z-score)
  - Commission: 4 RUB/contract × 2 sides
  - Result: +7.9%, DD −2.1%, WR 61%, 23 trades over ~5yr
  - Sharpe: 0.30 (best among all volume imbalance configs)

Usage:
  python3 strategies/si_volume_imbalance_strategy.py           # full backtest
  python3 strategies/si_volume_imbalance_strategy.py --signal  # current signal only
"""
import subprocess
import sys
import json
from datetime import datetime

import numpy as np
import pandas as pd
from clickhouse_driver import Client

CH_CLIENT = Client(host='10.0.0.60')
CH = ["clickhouse-client", "-h", "10.0.0.60", "-q"]

def q_df(sql):
    r = subprocess.run(CH + [sql], capture_output=True, text=True, timeout=120)
    if r.returncode:
        return None
    lines = [line.split("\t") for line in r.stdout.strip().split("\n") if line.strip()]
    if len(lines) < 2:
        return None
    return pd.DataFrame(lines[1:], columns=lines[0])


TICKER = "Si"
ASSET_CODE = "Si"
MARGIN = 7000
COMMISSION = 4.0
CAPITAL = 100_000
THRESH = 1.8  # tuned for DD ~15%
Z_WINDOW = 21
STOP_LOSS = None
FEATURE = "buy_pressure"
MIN_TRADES = 10


def load_features(asset_code=ASSET_CODE):
    """Load daily aggregated imbalance features from obstats_fo."""
    sql = """
        SELECT
            tradedate,
            avg(imb_l1) AS avg_imb_l1,
            avg(imb_l3) AS avg_imb_l3,
            max(abs(imb_l1)) AS max_imb,
            stddevSamp(imb_l1) AS imb_std,
            countIf(imb_l1 > 0.3) / count(*) AS buy_pressure,
            countIf(imb_l1 < -0.3) / count(*) AS sell_pressure
        FROM (
            SELECT
                tradedate,
                (COALESCE(vol_b_l1, 0) - COALESCE(vol_s_l1, 0))
                    / NULLIF(COALESCE(vol_b_l1, 0) + COALESCE(vol_s_l1, 0), 0) AS imb_l1,
                (COALESCE(vol_b_l3, 0) - COALESCE(vol_s_l3, 0))
                    / NULLIF(COALESCE(vol_b_l3, 0) + COALESCE(vol_s_l3, 0), 0) AS imb_l3
            FROM moex.obstats_fo
            WHERE asset_code = %(asset_code)s AND tradedate >= '2020-01-01'
        )
        GROUP BY tradedate
        ORDER BY tradedate
    """
    df = CH_CLIENT.query_dataframe(sql, params={'asset_code': asset_code})
    return df


def load_close(ticker=TICKER):
    """Load daily close prices from supercandles_fo."""
    sql = f"""
        SELECT
            toString(tradedate) as dt,
            toString(argMax(pr_close, tradetime)) as close
        FROM moex.supercandles_fo
        WHERE ticker = '{ticker}'
        GROUP BY tradedate
        ORDER BY tradedate
        FORMAT TabSeparatedWithNames
    """
    df = q_df(sql)
    if df is None or len(df) < 50:
        return None
    df['tradedate'] = pd.to_datetime(df['dt'])
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    return df[['tradedate', 'close']]


def run_backtest(show_details=True):
    """Run full backtest."""
    feat = load_features()
    if feat is None or len(feat) < 50:
        print("ERROR: No features data")
        return None, None, None
    
    closes = load_close()
    if closes is None or len(closes) < 50:
        print("ERROR: No close data")
        return None, None, None
    
    feat['tradedate'] = pd.to_datetime(feat['tradedate'])
    df = feat.merge(closes, on='tradedate', how='inner').sort_values('tradedate').reset_index(drop=True)
    if len(df) < 50:
        print(f"Too few merged rows: {len(df)}")
        return None, None, None
    
    for col in ['avg_imb_l1', 'avg_imb_l3', 'max_imb', 'imb_std', 'buy_pressure', 'sell_pressure']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df['ret'] = df['close'].pct_change()
    df['ret_next'] = df['ret'].shift(-1)
    df = df.dropna(subset=['ret_next']).reset_index(drop=True)
    
    mean_ = df[FEATURE].rolling(Z_WINDOW).mean()
    std_ = df[FEATURE].rolling(Z_WINDOW).std().replace(0, np.nan)
    df['feat_z'] = (df[FEATURE] - mean_) / std_
    
    d = df.dropna(subset=['feat_z']).copy()
    if len(d) < MIN_TRADES:
        print(f"Too few bars after z-score: {len(d)}")
        return None, None, None
    
    d['year'] = d['tradedate'].dt.year
    
    sig = np.zeros(len(d))
    sig[d['feat_z'].values > THRESH] = 1
    sig[d['feat_z'].values < -THRESH] = -1
    
    eq = [CAPITAL]
    trades = []
    for i in range(len(d)):
        s = sig[i]
        if s != 0 and not pd.isna(d['ret_next'].iloc[i]):
            r = d['ret_next'].iloc[i] * s
            n_cont = max(1, int(eq[-1] / MARGIN))
            comm_pct = (n_cont * COMMISSION * 2) / eq[-1]
            r_net = r - comm_pct
            if STOP_LOSS is not None and r_net < -STOP_LOSS:
                r_net = -STOP_LOSS
            eq.append(eq[-1] * (1 + r_net))
            trades.append({
                'dt': str(d['tradedate'].iloc[i].date()),
                'dir': 'LONG' if s == 1 else 'SHORT',
                'close': float(d['close'].iloc[i]),
                'feat_z': float(round(d['feat_z'].iloc[i], 2)),
                'buy_pressure': float(round(d[FEATURE].iloc[i], 6)),
                'ret_gross_pct': round(r * 100, 3),
                'ret_net_pct': round(r_net * 100, 3),
                'comm_pct': round(comm_pct * 100, 4),
                'n_cont': n_cont,
                'year': int(d['year'].iloc[i]),
            })
        else:
            eq.append(eq[-1])
    
    ret_tot = (eq[-1] / CAPITAL - 1) * 100
    peak = np.maximum.accumulate(eq)
    dd_vals = [(eq[i] / peak[i] - 1) * 100 for i in range(1, len(eq))]
    max_dd = min(dd_vals)
    
    df_t = pd.DataFrame(trades)
    metrics = {
        'ticker': TICKER,
        'feature': FEATURE,
        'threshold': THRESH,
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
        'period_start': str(d['tradedate'].iloc[0].date()),
        'period_end': str(d['tradedate'].iloc[-1].date()),
        'feature_bars': len(feat),
        'close_bars': len(closes),
        'merged_bars': len(df),
    }
    
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
    
    if show_details:
        print("=" * 70)
        print("Si (USD/RUB) Volume Imbalance Strategy")
        print("=" * 70)
        print(f"Feature:  {metrics['feature']}")
        print(f"Period:   {metrics['period_start']} -> {metrics['period_end']}")
        print(f"Capital:  {metrics['capital']:,} RUB")
        print(f"Margin:   {metrics['margin']:,} RUB/cont -> {int(CAPITAL/MARGIN)} cont")
        print(f"Commission: {metrics['commission_per_contract']} RUB/cont/side")
        print(f"Threshold: z-score > {metrics['threshold']}")
        print(f"Stop-loss: {'OFF' if STOP_LOSS is None else str(round(STOP_LOSS*100))+'%'}")
        print(f"Feature bars: {metrics['feature_bars']}, Merged: {metrics['merged_bars']}")
        print()
        print(f"  Trades:      {metrics['n_trades']}")
        print(f"  Total ret:   {metrics['total_ret_pct']:+.1f}%")
        print(f"  Max DD:      {metrics['max_dd_pct']:.1f}%")
        print(f"  Win rate:    {metrics['wr_pct']:.0f}%")
        print(f"  Sharpe:      {metrics['sharpe']:.3f}")
        print(f"  Final eq:    {metrics['final_equity']:,.0f} RUB")
        print(f"  Total comm:  {metrics['total_commission_pct']:.2f}%")
        print()
        print("  By year:")
        for yr, yd in sorted(metrics['by_year'].items()):
            st = "OK" if yd['ret_pct'] > 0 else "NO"
            print(f"    {st} {yr}: n={yd['trades']:<3} ret={yd['ret_pct']:+.1f}% wr={yd['wr_pct']:.0f}%")
        wf_pass = sum(1 for y in metrics['by_year'].values() if y['ret_pct'] > 0)
        print(f"    WF: {wf_pass}/{len(metrics['by_year'])} folds positive")
        
        last = d.iloc[-1]
        direction = 'NONE'
        if last['feat_z'] > THRESH:
            direction = 'LONG'
        elif last['feat_z'] < -THRESH:
            direction = 'SHORT'
        print(f"\n  Current signal ({last['tradedate'].date()}):")
        print(f"    Close: {last['close']:.2f}")
        print(f"    buy_pressure z: {last['feat_z']:.2f}")
        print(f"    Signal: {direction}")
    
    return metrics, trades, d


def save_signal():
    feat = load_features()
    closes = load_close()
    if feat is None or closes is None:
        return None
    feat['tradedate'] = pd.to_datetime(feat['tradedate'])
    df = feat.merge(closes, on='tradedate', how='inner').sort_values('tradedate').reset_index(drop=True)
    if len(df) < 50:
        return None
    for col in ['avg_imb_l1', 'avg_imb_l3', 'max_imb', 'imb_std', 'buy_pressure', 'sell_pressure']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['ret'] = df['close'].pct_change()
    df['ret_next'] = df['ret'].shift(-1)
    df = df.dropna(subset=['ret_next']).reset_index(drop=True)
    mean_ = df[FEATURE].rolling(Z_WINDOW).mean()
    std_ = df[FEATURE].rolling(Z_WINDOW).std().replace(0, np.nan)
    df['feat_z'] = (df[FEATURE] - mean_) / std_
    d = df.dropna(subset=['feat_z']).copy()
    if len(d) < 5:
        return None
    last = d.iloc[-1]
    signal = 0; direction = 'NONE'
    if last['feat_z'] > THRESH:
        signal = 1; direction = 'LONG'
    elif last['feat_z'] < -THRESH:
        signal = -1; direction = 'SHORT'
    n_cont = max(1, int(CAPITAL / MARGIN))
    sig = {
        'ticker': TICKER, 'strategy': 'volume_imbalance', 'feature': FEATURE,
        'timestamp': datetime.now().isoformat(),
        'last_date': str(last['tradedate'].date()),
        'last_close': float(round(last['close'], 2)),
        'feat_z': float(round(last['feat_z'], 2)),
        'buy_pressure': float(round(last[FEATURE], 6)),
        'signal': signal, 'direction': direction,
        'contracts': n_cont, 'margin_per_cont': MARGIN,
        'commission_per_trade': n_cont * COMMISSION * 2, 'threshold': THRESH,
    }
    bt_metrics, _, _ = run_backtest(show_details=False)
    if bt_metrics:
        sig.update({
            'backtest_ret_pct': bt_metrics['total_ret_pct'],
            'backtest_dd_pct': bt_metrics['max_dd_pct'],
            'backtest_wr_pct': bt_metrics['wr_pct'],
            'backtest_trades': bt_metrics['n_trades'],
            'backtest_period': f"{bt_metrics['period_start']} -> {bt_metrics['period_end']}",
        })
    with open('/home/user/strategies/si_volume_imbalance_signal.json', 'w') as f:
        json.dump(sig, f, indent=2, ensure_ascii=False)
    print(f"Signal saved to /home/user/strategies/si_volume_imbalance_signal.json")
    return sig


if __name__ == '__main__':
    if '--signal' in sys.argv:
        save_signal()
    else:
        metrics, trades, d = run_backtest()
        if metrics and d is not None and len(d) > 0:
            last = d.iloc[-1]
            direction = 'NONE'
            if last['feat_z'] > THRESH:
                direction = 'LONG'
            elif last['feat_z'] < -THRESH:
                direction = 'SHORT'
            print(f"\n  To save signal: python3 {__file__} --signal")
