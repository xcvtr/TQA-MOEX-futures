#!/usr/bin/env python3 -u
"""Per-ticker TF sweep for all strategies (VWAP, Reversion, OI Div)."""
import os, sys, warnings, time
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings('ignore')

from trading_bot.reversion_engine import load_price_data as load_rev_data
from trading_bot.reversion_engine import detect_mean_reversion_signals_limit
from trading_bot.vwap_engine import load_price_data as load_vwap_data
from trading_bot.vwap_engine import detect_vwap_signals_limit
from trading_bot.new_strategies import load_ohlcv, load_oi, merge_ohlcv_oi
from trading_bot.new_strategies import detect_oi_divergence_signals_limit
from trading_bot import DEFAULT_REVERSION_CONFIG, DEFAULT_VWAP_CONFIG, DEFAULT_OI_DIVERGENCE_CONFIG
from trading_bot import REVERSION_TICKERS, VWAP_TICKERS, OI_DIVERGENCE_TICKERS

PF_CAP = 999.99
OUTPUT_DIR = '/home/user/projects/TQA-MOEX/docs/plans/tf_sweep_results'

TF_RULES = {
    '5m':  None,
    '15m': '15min',
    '30m': '30min',
    'H1':  '1h',
}

def compute_stats(signals):
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'avg_return': 0.0, 'max_dd': 0.0}
    returns = np.array([s['return_pct'] for s in signals])
    n = len(returns)
    if n < 30:
        return {'n': n, 'wr': 0.0, 'pf': 0.0, 'avg_return': 0.0, 'max_dd': 0.0}
    wr = float(np.mean(returns > 0) * 100)
    gains = np.sum(returns[returns > 0])
    losses = np.abs(np.sum(returns[returns < 0]))
    pf = min(gains / losses, PF_CAP) if losses > 0 else PF_CAP
    avg_return = float(np.mean(returns))
    cum = np.cumsum(returns)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum)) if len(cum) > 0 else 0.0
    return {'n': n, 'wr': round(wr, 2), 'pf': round(pf, 4), 'avg_return': round(avg_return, 4), 'max_dd': round(max_dd, 4)}

def resample_ohlcv_tuple(rows, rule):
    """Resample to tuples (for reversion engine)."""
    if rule is None:
        return rows
    df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume'])
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    df = df.resample(rule).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    return [(idx.isoformat(), r['open'], r['high'], r['low'], r['close'], r['volume']) for idx, r in df.iterrows()]

def resample_ohlcv_dict(rows, rule):
    """Resample to dicts (for VWAP engine)."""
    if rule is None:
        return rows
    df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume'])
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    df = df.resample(rule).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    return [{'time': idx.isoformat(), 'open': r['open'], 'high': r['high'],
             'low': r['low'], 'close': r['close'], 'volume': r['volume']}
            for idx, r in df.iterrows()]

def test_vwap():
    results = []
    for sym, cfg in VWAP_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        base_cfg = {**DEFAULT_VWAP_CONFIG, **cfg}
        rows = load_vwap_data(sym, days=730)
        if not rows or len(rows) < 100:
            continue
        for tf_name, tf_rule in TF_RULES.items():
            tf_rows = resample_ohlcv_dict(rows, tf_rule)
            if len(tf_rows) < 50:
                continue
            for horizon in [6, 12, 24]:
                base_cfg['horizon'] = horizon
                sigs = detect_vwap_signals_limit(sym, tf_rows, base_cfg)
                st = compute_stats(sigs)
                if st['n'] >= 30:
                    results.append({'strategy':'VWAP','ticker':sym,'tf':tf_name,'horizon':horizon,**st})
                    print(f"  VWAP {sym:8s} {tf_name:4s} h={horizon:2d}: n={st['n']:5d} WR={st['wr']:5.1f}%")
    return results

def test_reversion():
    results = []
    for sym, cfg in REVERSION_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        base_cfg = {**DEFAULT_REVERSION_CONFIG, **cfg}
        rows = load_rev_data(sym, days=730)
        if not rows or len(rows) < 100:
            continue
        for tf_name, tf_rule in TF_RULES.items():
            tf_rows = resample_ohlcv_tuple(rows, tf_rule)
            if len(tf_rows) < 50:
                continue
            for horizon in [6, 12]:
                base_cfg['horizon'] = horizon
                sigs = detect_mean_reversion_signals_limit(sym, tf_rows, base_cfg)
                st = compute_stats(sigs)
                if st['n'] >= 30:
                    results.append({'strategy':'Reversion','ticker':sym,'tf':tf_name,'horizon':horizon,**st})
                    print(f"  REV {sym:8s} {tf_name:4s} h={horizon:2d}: n={st['n']:5d} WR={st['wr']:5.1f}%")
    return results

def test_oi():
    results = []
    for sym, cfg in OI_DIVERGENCE_TICKERS.items():
        if not cfg.get('enabled', True):
            continue
        base_cfg = {**DEFAULT_OI_DIVERGENCE_CONFIG, **cfg}
        for tf_name, tf_rule in TF_RULES.items():
            ohlcv = load_ohlcv(sym, days=730)
            oi = load_oi(sym, days=730)
            if not ohlcv or not oi or len(ohlcv) < 100:
                continue
            merged = merge_ohlcv_oi(ohlcv, oi)
            if not merged or len(merged) < 50:
                continue
            for horizon in [3, 6, 12]:
                base_cfg['horizon'] = horizon
                sigs = detect_oi_divergence_signals_limit(merged, base_cfg)
                st = compute_stats(sigs)
                if st['n'] >= 30:
                    results.append({'strategy':'OI_Div','ticker':sym,'tf':tf_name,'horizon':horizon,**st})
                    print(f"  OI  {sym:8s} {tf_name:4s} h={horizon:2d}: n={st['n']:5d} WR={st['wr']:5.1f}%")
    return results

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    t0 = time.time()
    all_results = []

    print("="*60)
    print("VWAP Deviation TF sweep")
    print("="*60)
    all_results.extend(test_vwap())

    print("="*60)
    print("Mean Reversion TF sweep")
    print("="*60)
    all_results.extend(test_reversion())

    print("="*60)
    print("OI Divergence TF sweep")
    print("="*60)
    all_results.extend(test_oi())

    if not all_results:
        print("No results!")
        sys.exit(1)

    df = pd.DataFrame(all_results)
    df.to_csv(os.path.join(OUTPUT_DIR, 'tf_sweep.csv'), index=False)

    print("\n" + "=" * 80)
    print("BEST PER TICKER")
    print("=" * 80)
    best = df.loc[df.groupby(['strategy','ticker'])['wr'].idxmax()]
    hdr = f"{'Strategy':12s} {'Ticker':8s} {'TF':4s} {'H':3s} {'N':>6s} {'WR%':>7s} {'PF':>8s} {'AvgRet':>9s}"
    print(hdr)
    print("-" * 65)
    for _, row in best.sort_values('wr', ascending=False).iterrows():
        print(f"{row['strategy']:12s} {row['ticker']:8s} {row['tf']:4s} {int(row['horizon']):3d} {int(row['n']):6d} {row['wr']:7.2f} {row['pf']:8.2f} {row['avg_return']:9.4f}")

    print(f"\nTime: {time.time()-t0:.1f}s")
    print(f"Saved to {OUTPUT_DIR}/tf_sweep.csv")

if __name__ == '__main__':
    main()
