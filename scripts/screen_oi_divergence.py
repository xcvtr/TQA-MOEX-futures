#!/usr/bin/env python3
"""
Phase 1: Screen all 64 tickers for OI Divergence WR.
For each ticker: WR, avgRet, DD for horizons 6, 12, 24.
Saves to docs/plans/strategy_v3/oi_screening.txt
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from datetime import datetime, timedelta, timezone
from typing import List, Dict

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

def load_ohlcv(symbol, days=365):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s
        ORDER BY time
    """, (symbol, since))
    rows = []
    for r in cur:
        rows.append({
            'time': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
            'open': float(r[1]), 'high': float(r[2]),
            'low': float(r[3]), 'close': float(r[4]),
            'volume': float(r[5]), 'symbol': symbol,
        })
    cur.close(); conn.close()
    return rows

def load_oi(symbol, days=365):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
        FROM moex_prices_5m_oi
        WHERE symbol = %s AND time >= %s
        ORDER BY time
    """, (symbol, since))
    rows = []
    for r in cur:
        rows.append({
            'time': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
            'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]),
            'yur_buy': float(r[3]), 'yur_sell': float(r[4]),
            'total_oi': float(r[5]),
        })
    cur.close(); conn.close()
    return rows

def merge_ohlcv_oi(ohlcv, oi):
    oi_by_time = {r['time'][:16]: r for r in oi}
    merged = []
    for r in ohlcv:
        oi_row = oi_by_time.get(r['time'][:16])
        if oi_row is None: continue
        merged.append({**r, **oi_row})
    return merged

def detect_oi_divergence_signals(merged, config=None):
    default = {'lookback': 20, 'horizon': 6, 'extreme_window': 10,
               'bear_threshold': 0.95, 'bull_threshold': 1.05}
    config = {**default, **(config or {})}
    n = len(merged)
    if n < 50: return []
    closes = [r['close'] for r in merged]
    oi_vals = [r['total_oi'] for r in merged]
    lookback = config['lookback']; ext_w = config['extreme_window']
    horizon = config['horizon']; bear_th = config['bear_threshold']
    bull_th = config['bull_threshold']
    signals = []
    min_idx = lookback + 5
    for i in range(min_idx, n):
        if i + 1 >= n: break
        if i + horizon >= n: continue
        search_start = max(0, i - lookback)
        search_end = max(search_start + 1, i - ext_w)
        if search_end <= search_start: continue
        max_idx = search_start
        min_idx_val = search_start
        for j in range(search_start, search_end):
            if closes[j] > closes[max_idx]: max_idx = j
            if closes[j] < closes[min_idx_val]: min_idx_val = j
        direction = None
        if closes[i] > closes[max_idx] and oi_vals[i] < oi_vals[max_idx] * bear_th:
            direction = 'SHORT'
        elif closes[i] < closes[min_idx_val] and oi_vals[i] > oi_vals[min_idx_val] * bull_th:
            direction = 'LONG'
        if direction is None: continue
        entry = merged[i+1]['open']
        if entry <= 0: continue
        exit_price = merged[i+horizon]['close'] if i+horizon < n else merged[-1]['close']
        if direction == 'LONG': ret = (exit_price - entry) / entry * 100
        else: ret = (entry - exit_price) / entry * 100
        signals.append({
            'ticker': merged[0].get('symbol','?'), 'direction': direction,
            'entry': round(entry,4), 'exit': round(exit_price,4),
            'time': merged[i]['time'], 'return_pct': round(ret,4),
            'strategy': 'oi_divergence', 'idx': i,
        })
    return signals

def compute_stats(signals):
    if not signals: return {'n':0,'wr':0.0,'pf':0.0,'dd':0.0,'avg_ret':0.0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins)/n*100 if n>0 else 0.0
    sum_wins = sum(wins) if wins else 0.0
    sum_losses = abs(sum(losses)) if losses else 0.0
    pf = sum_wins/sum_losses if sum_losses>0 else (sum_wins if sum_wins>0 else 0.0)
    cum = 0.0; peak = 0.0; max_dd = 0.0
    for r in returns:
        cum += r
        if cum > peak: peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd: max_dd = dd
    return {'n':n,'wr':round(wr,1),'pf':round(pf,2),'dd':round(max_dd,1),'avg_ret':round(sum(returns)/n,2)}

def get_symbols():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m_oi ORDER BY symbol")
    rows = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows

def main():
    symbols = get_symbols()
    print(f"Total symbols: {len(symbols)}")
    horizons = [6, 12, 24]
    results = {}

    for sym in symbols:
        print(f"  Processing {sym}...")
        try:
            ohlcv = load_ohlcv(sym, 365)
            if not ohlcv or len(ohlcv) < 100:
                print(f"    ⚠ Not enough OHLCV data")
                continue
            oi = load_oi(sym, 365)
            if not oi:
                print(f"    ⚠ No OI data")
                continue
            merged = merge_ohlcv_oi(ohlcv, oi)
            if not merged or len(merged) < 100:
                print(f"    ⚠ Not enough merged data")
                continue
            ticker_results = {}
            for h in horizons:
                sigs = detect_oi_divergence_signals(merged, {'horizon': h})
                stats = compute_stats(sigs)
                ticker_results[f'h={h}'] = stats
            results[sym] = ticker_results
            best_h = max(ticker_results, key=lambda k: ticker_results[k]['wr'])
            print(f"    sigs={ticker_results[best_h]['n']} WR={ticker_results[best_h]['wr']}% best={best_h}")
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'plans', 'strategy_v3')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'oi_screening.txt')

    lines = []
    lines.append("=" * 70)
    lines.append("  OI Divergence — Per-Ticker Screening (365 days)")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Tickers: {len(results)}")
    lines.append("=" * 70)
    lines.append("")

    header = f"{'Ticker':<10} {'Horizon':<10} {'Sig':<8} {'WR%':<8} {'AvgRet%':<10} {'DD%':<8} {'PF':<8}"
    lines.append(header)
    lines.append("-" * 70)

    qualified = []
    for sym in sorted(results.keys()):
        best_h = max(results[sym], key=lambda k: results[sym][k]['wr'])
        r = results[sym][best_h]
        lines.append(f"{sym:<10} {best_h:<10} {r['n']:<8} {r['wr']:<8} {r['avg_ret']:<10} {r['dd']:<8} {r['pf']:<8}")
        if r['wr'] > 52 and r['n'] >= 20:
            qualified.append((sym, best_h, r))

    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  Qualified (WR>52%, sig>=20): {len(qualified)} tickers")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"{'Ticker':<10} {'Horizon':<10} {'Sig':<8} {'WR%':<8} {'AvgRet%':<10} {'DD%':<8} {'PF':<8}")
    lines.append("-" * 70)
    for sym, h, r in sorted(qualified, key=lambda x: x[2]['wr'], reverse=True):
        lines.append(f"{sym:<10} {h:<10} {r['n']:<8} {r['wr']:<8} {r['avg_ret']:<10} {r['dd']:<8} {r['pf']:<8}")

    lines.append("")
    lines.append("── Per-ticker detail ──")
    lines.append("")
    for sym in sorted(results.keys()):
        lines.append(f"  {sym}:")
        for h in horizons:
            r = results[sym][f'h={h}']
            lines.append(f"    {h:<10} sig={r['n']:<5} WR={r['wr']:<6}% avgRet={r['avg_ret']:<8} DD={r['dd']:<6}% PF={r['pf']:<6}")
        lines.append("")

    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\n✅ Saved {out_path}")
    print(f"Qualified tickers (WR>52%): {len(qualified)}")

if __name__ == '__main__':
    main()
