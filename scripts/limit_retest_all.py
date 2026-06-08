#!/usr/bin/env python3 -u
"""Limit retest for all 4 strategies — market vs limit entry comparison."""
import os, sys, warnings, time
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings('ignore')

from trading_bot.scanner import load_data as load_vs_data
from trading_bot.engine import detect_signals, detect_signals_limit
from trading_bot.reversion_engine import load_price_data as load_rev_data
from trading_bot.reversion_engine import detect_mean_reversion_signals, detect_mean_reversion_signals_limit
from trading_bot.vwap_engine import load_price_data as load_vwap_data
from trading_bot.vwap_engine import detect_vwap_signals, detect_vwap_signals_limit
from trading_bot.new_strategies import load_ohlcv, load_oi, merge_ohlcv_oi
from trading_bot.new_strategies import detect_oi_divergence_signals, detect_oi_divergence_signals_limit
from trading_bot import (
    DEFAULT_CONFIG, DEFAULT_REVERSION_CONFIG, DEFAULT_VWAP_CONFIG, DEFAULT_OI_DIVERGENCE_CONFIG,
    SCAN_SYMBOLS, REVERSION_TICKERS, VWAP_TICKERS, OI_DIVERGENCE_TICKERS,
)

PF_CAP = 999.99
OUTPUT_DIR = '/home/user/projects/TQA-MOEX/docs/plans/limit_retest_results'


def compute_stats(signals):
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'avg_return': 0.0, 'max_dd': 0.0, 'fill_rate': 0.0}
    returns = np.array([s['return_pct'] for s in signals])
    n = len(returns)
    wr = float(np.mean(returns > 0) * 100)
    gains = np.sum(returns[returns > 0])
    losses = np.abs(np.sum(returns[returns < 0]))
    pf = min(gains / losses, PF_CAP) if losses > 0 else PF_CAP
    avg_return = float(np.mean(returns))
    cum = np.cumsum(returns)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum)) if len(cum) > 0 else 0.0
    return {
        'n': n, 'wr': round(wr, 2), 'pf': round(pf, 4),
        'avg_return': round(avg_return, 4), 'max_dd': round(max_dd, 4),
    }


def compute_fill_rate(signals, total_market_signals):
    if total_market_signals == 0:
        return 0.0
    return len(signals) / total_market_signals * 100


def run_vs(ticker, config):
    cfg = {**DEFAULT_CONFIG, **config}
    rows = load_vs_data(ticker, days=730)
    if len(rows) < 50:
        return None, None
    market_sigs = detect_signals(rows, cfg)
    limit_sigs = detect_signals_limit(rows, cfg)
    fill_rate = compute_fill_rate(limit_sigs, len(market_sigs))
    return market_sigs, limit_sigs, fill_rate


def run_reversion(ticker, config):
    cfg = {**DEFAULT_REVERSION_CONFIG, **config}
    rows = load_rev_data(ticker, days=730)
    if len(rows) < 50:
        return None, None, 0.0
    market_sigs = detect_mean_reversion_signals(ticker, rows, cfg)
    limit_sigs = detect_mean_reversion_signals_limit(ticker, rows, cfg)
    total_market = len(market_sigs)
    if total_market == 0:
        return market_sigs, limit_sigs, 0.0
    fill_rate = compute_fill_rate(limit_sigs, total_market)
    return market_sigs, limit_sigs, fill_rate


def run_vwap(ticker, config):
    cfg = {**DEFAULT_VWAP_CONFIG, **config}
    rows = load_vwap_data(ticker, days=730)
    if len(rows) < 50:
        return None, None, 0.0
    market_sigs = detect_vwap_signals(ticker, rows, cfg)
    limit_sigs = detect_vwap_signals_limit(ticker, rows, cfg)
    total_market = len(market_sigs)
    if total_market == 0:
        return market_sigs, limit_sigs, 0.0
    fill_rate = compute_fill_rate(limit_sigs, total_market)
    return market_sigs, limit_sigs, fill_rate


def run_oi_div(ticker, config):
    cfg = {**DEFAULT_OI_DIVERGENCE_CONFIG, **config}
    ohlcv = load_ohlcv(ticker, days=730)
    oi = load_oi(ticker, days=730)
    if not ohlcv or not oi or len(ohlcv) < 50:
        return None, None, 0.0
    merged = merge_ohlcv_oi(ohlcv, oi)
    if len(merged) < 50:
        return None, None, 0.0
    market_sigs = detect_oi_divergence_signals(merged, cfg)
    limit_sigs = detect_oi_divergence_signals_limit(merged, cfg)
    total_market = len(market_sigs)
    if total_market == 0:
        return market_sigs, limit_sigs, 0.0
    fill_rate = compute_fill_rate(limit_sigs, total_market)
    return market_sigs, limit_sigs, fill_rate


STRATEGIES = [
    {
        'name': 'Volume Surge',
        'tickers': SCAN_SYMBOLS,
        'horizons': [6, 12, 24],
        'runner': run_vs,
        'base_config': DEFAULT_CONFIG,
    },
    {
        'name': 'Mean Reversion',
        'tickers': list(REVERSION_TICKERS.keys()),
        'horizons': [6, 12],
        'runner': run_reversion,
        'base_config': DEFAULT_REVERSION_CONFIG,
    },
    {
        'name': 'VWAP Deviation',
        'tickers': list(VWAP_TICKERS.keys()),
        'horizons': [6, 12, 24],
        'runner': run_vwap,
        'base_config': DEFAULT_VWAP_CONFIG,
    },
    {
        'name': 'OI Divergence',
        'tickers': list(OI_DIVERGENCE_TICKERS.keys()),
        'horizons': [3, 6, 12],
        'runner': run_oi_div,
        'base_config': DEFAULT_OI_DIVERGENCE_CONFIG,
    },
]


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    t0 = time.time()

    all_rows = []

    for strat in STRATEGIES:
        name = strat['name']
        tickers = strat['tickers']
        horizons = strat['horizons']
        runner = strat['runner']

        print(f"\n{'='*80}")
        print(f"  {name} ({', '.join(tickers)})")
        print(f"{'='*80}")

        for ticker in tickers:
            for h in horizons:
                result = runner(ticker, {'horizon': h})
                if result is None or result[0] is None:
                    continue
                market_sigs, limit_sigs, fill_rate = result

                m_stats = compute_stats(market_sigs)
                l_stats = compute_stats(limit_sigs)

                delta_wr = round(l_stats['wr'] - m_stats['wr'], 2) if m_stats['n'] > 0 else 0.0

                all_rows.append({
                    'strategy': name,
                    'ticker': ticker,
                    'entry_type': 'market',
                    'horizon': h,
                    'n': m_stats['n'],
                    'wr': m_stats['wr'],
                    'pf': m_stats['pf'],
                    'avg_return': m_stats['avg_return'],
                    'max_dd': m_stats['max_dd'],
                    'fill_rate': 100.0,
                })
                all_rows.append({
                    'strategy': name,
                    'ticker': ticker,
                    'entry_type': 'limit',
                    'horizon': h,
                    'n': l_stats['n'],
                    'wr': l_stats['wr'],
                    'pf': l_stats['pf'],
                    'avg_return': l_stats['avg_return'],
                    'max_dd': l_stats['max_dd'],
                    'fill_rate': round(fill_rate, 1),
                })

                bar = '│'
                print(
                    f"  {ticker:8s} {bar} "
                    f"Market: n={m_stats['n']:4d} WR={m_stats['wr']:6.1f}% PF={m_stats['pf']:6.2f}  {bar} "
                    f"Limit: n={l_stats['n']:4d} WR={l_stats['wr']:6.1f}% PF={l_stats['pf']:6.2f} "
                    f"Fill={fill_rate:5.1f}% ΔWR={delta_wr:+.1f}%"
                )

    if not all_rows:
        print("\nNo results generated!")
        return

    df = pd.DataFrame(all_rows)

    # Save full CSV
    csv_path = os.path.join(OUTPUT_DIR, 'market_vs_limit_comparison.csv')
    df.to_csv(csv_path, index=False)
    print(f"\nSaved CSV → {csv_path}")

    # Print comparison table
    print("\n")
    print("=" * 100)
    print("  MARKET vs LIMIT COMPARISON")
    print("=" * 100)
    hdr = f"{'Strategy':<18} {'Ticker':<8} {'H':<3} {'Market WR':<10} {'Limit WR':<10} {'Fill%':<7} {'ΔWR':<7} {'Mkt n':<6} {'Lim n':<6}"
    print(hdr)
    print("-" * 100)

    summary_rows = []
    for strat in STRATEGIES:
        name = strat['name']
        sub = df[(df['strategy'] == name) & (df['entry_type'] == 'limit')]
        for _, row in sub.iterrows():
            ticker = row['ticker']
            h = int(row['horizon'])
            m_row = df[(df['strategy'] == name) & (df['ticker'] == ticker) & (df['horizon'] == h) & (df['entry_type'] == 'market')]
            if m_row.empty:
                continue
            m_wr = m_row.iloc[0]['wr']
            m_n = int(m_row.iloc[0]['n'])
            delta_wr = row['wr'] - m_wr
            print(
                f"  {name:<18} {ticker:<8} {h:<3} {m_wr:<10.1f} {row['wr']:<10.1f} "
                f"{row['fill_rate']:<7.1f} {delta_wr:<+7.1f} {m_n:<6} {int(row['n']):<6}"
            )
            summary_rows.append({
                'strategy': name, 'ticker': ticker, 'horizon': h,
                'market_wr': m_wr, 'limit_wr': row['wr'],
                'fill_rate': row['fill_rate'], 'delta_wr': delta_wr,
                'market_n': m_n, 'limit_n': int(row['n']),
            })

    print("-" * 100)

    # Per-strategy avg
    print("")
    for strat in STRATEGIES:
        name = strat['name']
        sub = [r for r in summary_rows if r['strategy'] == name and r['limit_n'] >= 50]
        if sub:
            avg_m_wr = np.mean([r['market_wr'] for r in sub])
            avg_l_wr = np.mean([r['limit_wr'] for r in sub])
            avg_fill = np.mean([r['fill_rate'] for r in sub])
            total_m = sum(r['market_n'] for r in sub)
            total_l = sum(r['limit_n'] for r in sub)
            print(
                f"  {name:<18} avg: Market WR={avg_m_wr:.1f}% → Limit WR={avg_l_wr:.1f}% "
                f"Fill={avg_fill:.1f}%  total_m={total_m} total_l={total_l}"
            )

    elapsed = time.time() - t0
    print(f"\n  Time: {elapsed/60:.1f} min")
    print(f"  File: {csv_path}")


if __name__ == '__main__':
    main()
