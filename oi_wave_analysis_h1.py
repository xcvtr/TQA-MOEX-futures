#!/usr/bin/env python3
"""
OI Wave Analysis on H1 timeframe — v4 (FINAL).
Uses percentile-based approach on cumulative OI ratio.

Key insight: prices_5m_oi has cumulative values. The ratio 
yur_ratio = (yur_buy + yur_sell) / (total_oi + 1) measures 
yur participation share. When this shifts significantly from 
its recent norm for several hours, it signals positioning change.

Instead of z-score (which fails on non-normal distributions), 
use percentile rank over a rolling window:
- bars in top 10% of yur_ratio → yur dominant → expect LONG
- bars in bottom 10% → fiz dominant → expect SHORT

Usage: python3 oi_wave_analysis_h1.py
"""

import clickhouse_connect
import json
import os
import sys
import time
import traceback
from collections import OrderedDict
import warnings
warnings.filterwarnings('ignore')

START_DATE = '2024-06-01'
END_DATE = '2026-06-01'
PERCENTILE_THRESHOLD = 85  # top/bottom % for wave detection
MIN_WAVE_HOURS = 3
FORWARD_HOURS = [6, 12, 24]
WINDOW = 120  # rolling window for percentile (5 days of H1)
TIME_LIMIT_SECONDS = 115
MAX_TICKERS = 64

REPORTS_DIR = '/home/user/projects/TQA-MOEX/reports/oi_wave_analysis'

def get_client():
    return clickhouse_connect.get_client(host='localhost', port=8123)

def get_all_tickers(client):
    r = client.query('SELECT DISTINCT symbol FROM moex.prices_5m_oi ORDER BY symbol')
    return [row[0] for row in r.result_rows]

def load_data(client, symbol):
    """Load OI data using openinterest table (clgroup=0 fiz, clgroup=1 yur)."""
    # Use openinterest table which has buy_orders, sell_orders per clgroup
    oi_query = f"""
    SELECT time, clgroup, buy_orders, sell_orders
    FROM moex.openinterest
    WHERE symbol = '{symbol}'
      AND time >= '{START_DATE}'
      AND time < '{END_DATE}'
    ORDER BY time, clgroup
    """
    oi_raw = client.query(oi_query).result_rows
    if not oi_raw:
        return None, None

    # Pivot clgroup rows into columns per timestamp
    oi_data = OrderedDict()
    for row in oi_raw:
        dt = row[0]
        cg = row[1]  # 0=fiz, 1=yur
        buy = int(row[2])
        sell = int(row[3])
        if dt not in oi_data:
            oi_data[dt] = {'fiz_buy': 0, 'fiz_sell': 0, 'yur_buy': 0, 'yur_sell': 0}
        if cg == 0:
            oi_data[dt]['fiz_buy'] = buy
            oi_data[dt]['fiz_sell'] = sell
        else:
            oi_data[dt]['yur_buy'] = buy
            oi_data[dt]['yur_sell'] = sell

    price_query = f"""
    SELECT time, open, high, low, close, volume
    FROM moex.prices_5m
    WHERE symbol = '{symbol}'
      AND time >= '{START_DATE}'
      AND time < '{END_DATE}'
    ORDER BY time
    """
    price_data = client.query(price_query).result_rows
    if not price_data:
        return None, None

    return list(oi_data.values()), price_data, list(oi_data.keys())

def resample_to_h1(oi_data, price_data, oi_times):
    """Resample to H1 bars using LAST value of each hour for OI snapshots."""
    price_by_time = {}
    for row in price_data:
        dt = row[0]
        key = dt.replace(second=0, microsecond=0)
        if key not in price_by_time:
            price_by_time[key] = {
                'open': float(row[1]), 'high': float(row[2]),
                'low': float(row[3]), 'close': float(row[4]),
                'volume': int(row[5]) if row[5] else 0,
            }
        else:
            # Update H/L within hour
            h = float(row[2]) if row[2] else None
            l = float(row[3]) if row[3] else None
            if h is not None and (price_by_time[key]['high'] is None or h > price_by_time[key]['high']):
                price_by_time[key]['high'] = h
            if l is not None and (price_by_time[key]['low'] is None or l < price_by_time[key]['low']):
                price_by_time[key]['low'] = l

    # Group OI by hour, take last snapshot
    hour_bars = OrderedDict()
    for i, od in enumerate(oi_data):
        dt = oi_times[i]
        hk = dt.replace(minute=0, second=0, microsecond=0)
        # Always update to get the last snapshot in the hour
        hour_bars[hk] = {
            'fiz_buy': od['fiz_buy'], 'fiz_sell': od['fiz_sell'],
            'yur_buy': od['yur_buy'], 'yur_sell': od['yur_sell'],
        }

    merged = []
    for hk in sorted(hour_bars.keys()):
        hb = hour_bars[hk]
        pi = price_by_time.get(hk)
        if pi is None:
            # Try finding a close price nearby
            for minute in range(0, 60, 5):
                alt = hk.replace(minute=minute)
                if alt in price_by_time:
                    pi = price_by_time[alt]
                    break
        if pi is None or pi['open'] is None:
            continue

        total_fiz = hb['fiz_buy'] + hb['fiz_sell']
        total_yur = hb['yur_buy'] + hb['yur_sell']
        total_oi = total_fiz + total_yur

        if total_oi == 0:
            continue

        merged.append({
            'time': hk,
            'open': pi['open'], 'high': pi['high'], 'low': pi['low'],
            'close': pi['close'], 'volume': pi['volume'],
            'fiz_buy': hb['fiz_buy'], 'fiz_sell': hb['fiz_sell'],
            'yur_buy': hb['yur_buy'], 'yur_sell': hb['yur_sell'],
            'total_oi': total_oi,
            # Yur ratio = yur share of total OI at this snapshot
            'yur_ratio': total_yur / total_oi,
            # Fiz net = fiz_buy - fiz_sell as fraction of fiz OI
            'fiz_net_ratio': (hb['fiz_buy'] - hb['fiz_sell']) / max(total_fiz, 1),
            # Yur net = yur_buy - yur_sell as fraction of yur OI
            'yur_net_ratio': (hb['yur_buy'] - hb['yur_sell']) / max(total_yur, 1),
        })

    return merged

def get_percentile_rank(value, history):
    """Return percentile rank of value within history (0-100)."""
    if not history:
        return 50.0
    count_below = sum(1 for v in history if v < value)
    count_equal = sum(1 for v in history if v == value)
    return (count_below + 0.5 * count_equal) / len(history) * 100

def calc_indicators(bars):
    n = len(bars)
    if n < WINDOW + 10:
        return bars, False

    # Calculate percentile ranks using rolling history
    for i, b in enumerate(bars):
        if i < WINDOW:
            b['yur_ratio_pct'] = 50.0
            b['yur_net_pct'] = 50.0
            b['fiz_net_pct'] = 50.0
        else:
            hist_yur = [bars[j]['yur_ratio'] for j in range(i-WINDOW, i)]
            b['yur_ratio_pct'] = get_percentile_rank(b['yur_ratio'], hist_yur)
            
            hist_yur_net = [bars[j]['yur_net_ratio'] for j in range(i-WINDOW, i)]
            b['yur_net_pct'] = get_percentile_rank(b['yur_net_ratio'], hist_yur_net)
            
            hist_fiz_net = [bars[j]['fiz_net_ratio'] for j in range(i-WINDOW, i)]
            b['fiz_net_pct'] = get_percentile_rank(b['fiz_net_ratio'], hist_fiz_net)

        # Forward price changes
        close_i = b['close']
        if close_i is None or close_i == 0:
            for h in FORWARD_HOURS:
                b[f'price_change_{h}h'] = None
            continue
        for h in FORWARD_HOURS:
            j = i + h
            if j < n and bars[j]['close'] is not None and bars[j]['close'] > 0:
                b[f'price_change_{h}h'] = (bars[j]['close'] - close_i) / close_i * 100
            else:
                b[f'price_change_{h}h'] = None

    return bars, True

def detect_waves(bars):
    n = len(bars)
    if n < WINDOW + 10:
        return []

    waves = []
    in_wave = False
    wave_start = None
    wave_direction = None

    for i, b in enumerate(bars):
        yur_pct = b['yur_ratio_pct']
        
        # Signal: yur_ratio in extreme percentile
        signal = None
        if yur_pct > (100 - PERCENTILE_THRESHOLD):
            signal = 'long'   # yur unusually dominant → expect UP
        elif yur_pct < PERCENTILE_THRESHOLD:
            signal = 'short'  # fiz unusually dominant → expect DOWN
        
        is_active = signal is not None
        
        if is_active and not in_wave:
            in_wave = True
            wave_start = i
            wave_direction = signal
        elif (not is_active or signal != wave_direction) and in_wave:
            duration = i - wave_start
            if duration >= MIN_WAVE_HOURS:
                wave_bars = bars[wave_start:i]
                avg_yur_pct = sum(b.get('yur_ratio_pct', 50) for b in wave_bars) / len(wave_bars)
                expected_up = wave_direction == 'long'
                
                pre_start = max(0, wave_start - 6)
                pre_change = None
                if pre_start < wave_start and bars[wave_start]['close'] and bars[pre_start]['close']:
                    pre_change = (bars[wave_start]['close'] - bars[pre_start]['close']) / bars[pre_start]['close'] * 100
                
                wave_end = i - 1
                last_bar = bars[wave_end]
                
                waves.append({
                    'start_idx': wave_start, 'end_idx': wave_end,
                    'start_time': str(bars[wave_start]['time']),
                    'end_time': str(bars[wave_end]['time']),
                    'duration_hours': duration,
                    'direction': wave_direction,
                    'expected_up': expected_up,
                    'avg_yur_ratio_pct': round(avg_yur_pct, 1),
                    'pre_change_pct': round(pre_change, 2) if pre_change is not None else None,
                    'fc_6h': last_bar.get('price_change_6h'),
                    'fc_12h': last_bar.get('price_change_12h'),
                    'fc_24h': last_bar.get('price_change_24h'),
                })
            
            if is_active:
                in_wave = True
                wave_start = i
                wave_direction = signal
            else:
                in_wave = False
                wave_direction = None

    # Trailing wave
    if in_wave and wave_start is not None:
        duration = n - wave_start
        if duration >= MIN_WAVE_HOURS:
            wave_bars = bars[wave_start:]
            avg_yur_pct = sum(b.get('yur_ratio_pct', 50) for b in wave_bars) / len(wave_bars)
            waves.append({
                'start_idx': wave_start, 'end_idx': n - 1,
                'start_time': str(bars[wave_start]['time']),
                'end_time': str(bars[n-1]['time']),
                'duration_hours': duration, 'direction': wave_direction,
                'expected_up': wave_direction == 'long',
                'avg_yur_ratio_pct': round(avg_yur_pct, 1),
                'pre_change_pct': None,
                'fc_6h': None, 'fc_12h': None, 'fc_24h': None,
            })

    return waves

def compute_wave_metrics(waves):
    if not waves:
        return None
    n = len(waves)
    avg_duration = sum(w['duration_hours'] for w in waves) / n
    metrics = {'n_waves': n, 'avg_wave_hours': round(avg_duration, 1)}
    for h in FORWARD_HOURS:
        correct = 0
        total = 0
        total_return = 0.0
        rc = 0
        for w in waves:
            fc = w[f'fc_{h}h']
            if fc is not None:
                total += 1
                total_return += fc
                rc += 1
                if (w['expected_up'] and fc > 0) or (not w['expected_up'] and fc < 0):
                    correct += 1
        metrics[f'wave_accuracy_{h}h'] = round(correct / total * 100, 1) if total > 0 else 0
        metrics[f'mean_follow_through_{h}h'] = round(total_return / rc, 2) if rc > 0 else 0.0
        metrics[f'total_trades_{h}h'] = total
    return metrics

def analyze_ticker(client, symbol):
    print(f"  {symbol:>7s}...", end=" ")
    sys.stdout.flush()
    try:
        oi_data, price_data, oi_times = load_data(client, symbol)
        if oi_data is None:
            print("NO DATA")
            return None
        
        bars = resample_to_h1(oi_data, price_data, oi_times)
        if len(bars) < WINDOW + 20:
            print(f"few bars ({len(bars)})")
            return None

        bars, ok = calc_indicators(bars)
        if not ok:
            print("insufficient")
            return None

        waves = detect_waves(bars)
        if not waves:
            print("0 waves")
            return None

        metrics = compute_wave_metrics(waves)
        if metrics is None:
            print("no metrics")
            return None

        print(f"{metrics['n_waves']:3d}w  Acc6h={metrics['wave_accuracy_6h']:5.1f}%")
        
        return {
            'symbol': symbol, **metrics,
            'waves': [
                {'start': w['start_time'], 'end': w['end_time'],
                 'duration': w['duration_hours'], 'direction': w['direction'],
                 'avg_yur_ratio_pct': w['avg_yur_ratio_pct'],
                 'pre_change_pct': w['pre_change_pct'],
                 'fc_6h': w['fc_6h'], 'fc_12h': w['fc_12h'], 'fc_24h': w['fc_24h']}
                for w in waves if w['fc_6h'] is not None
            ][:20],
        }
    except Exception as e:
        print(f"ERR: {e}")
        traceback.print_exc()
        return None

def main():
    start = time.time()
    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    print("=" * 62)
    print("OI WAVE ANALYSIS (H1) v4 — percentile-based")
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"Signal: yur_ratio in top/bottom {100-PERCENTILE_THRESHOLD}% for >= {MIN_WAVE_HOURS}h")
    print(f"Window: {WINDOW} bars")
    print("=" * 62)
    
    client = get_client()
    tickers = get_all_tickers(client)
    if len(tickers) > MAX_TICKERS:
        print(f"Limiting to {MAX_TICKERS} tickers (of {len(tickers)})")
        tickers = tickers[:MAX_TICKERS]
    print(f"Tickers: {len(tickers)}")
    
    results = []
    for sym in tickers:
        if time.time() - start > TIME_LIMIT_SECONDS:
            print(f"\n⚠ Time limit")
            break
        r = analyze_ticker(client, sym)
        if r:
            results.append(r)
    
    elapsed = time.time() - start
    print(f"\n{'='*62}")
    print(f"Done: {len(results)}/{len(tickers)} tickers in {elapsed:.1f}s")
    
    if not results:
        print("No results!")
        return
    
    results.sort(key=lambda r: r['wave_accuracy_6h'], reverse=True)
    
    lines = []
    lines.append(f"\n{'='*62}")
    lines.append("=== TOP 10 — OI Wave Accuracy (H1) ===")
    lines.append(f"{'='*62}")
    h = f"{'#':>3s}  {'Ticker':>7s}  {'Waves':>6s}  {'AvgH':>5s}  {'Acc6h':>6s}  {'Acc12h':>7s}  {'Acc24h':>7s}  {'Ret6h':>7s}  {'Ret12h':>8s}  {'Ret24h':>8s}"
    lines.append(h)
    lines.append("-" * len(h))
    for i, r in enumerate(results[:10]):
        lines.append(
            f"{i+1:3d}  {r['symbol']:>7s}  {r['n_waves']:6d}  {r['avg_wave_hours']:5.1f}  "
            f"{r['wave_accuracy_6h']:5.1f}%  {r['wave_accuracy_12h']:5.1f}%  {r['wave_accuracy_24h']:5.1f}%  "
            f"{r['mean_follow_through_6h']:+6.2f}%  {r['mean_follow_through_12h']:+6.2f}%  {r['mean_follow_through_24h']:+6.2f}%"
        )
    
    lines.append(f"\n{'='*62}")
    lines.append("=== BOTTOM 5 ===")
    lines.append(f"{'='*62}")
    for i, r in enumerate(results[-5:]):
        idx = len(results) - 5 + i
        lines.append(
            f"{idx+1:3d}  {r['symbol']:>7s}  {r['n_waves']:6d}  {r['avg_wave_hours']:5.1f}  "
            f"{r['wave_accuracy_6h']:5.1f}%  {r['wave_accuracy_12h']:5.1f}%  {r['wave_accuracy_24h']:5.1f}%  "
            f"{r['mean_follow_through_6h']:+.2f}%  {r['mean_follow_through_12h']:+.2f}%  {r['mean_follow_through_24h']:+.2f}%"
        )
    
    # Duration histogram
    durs = [w['duration'] for r in results for w in r.get('waves', [])]
    if durs:
        min_d, max_d = min(durs), max(durs)
        bins = 8
        bw = max((max_d - min_d) / bins, 1)
        hist = [0] * bins
        for v in durs:
            idx = min(int((v - min_d) / bw), bins - 1)
            hist[idx] += 1
        mc = max(hist) or 1
        lines.append(f"\nWave Durations (hours):")
        for i in range(bins):
            lo = min_d + i * bw
            hi = lo + bw
            bar = "█" * int(hist[i] / mc * 40)
            lines.append(f"  {lo:.0f}-{hi:.0f}h | {bar} {hist[i]}")
    
    # Summary
    avg6 = sum(r['wave_accuracy_6h'] for r in results) / len(results)
    avg12 = sum(r['wave_accuracy_12h'] for r in results) / len(results)
    avg24 = sum(r['wave_accuracy_24h'] for r in results) / len(results)
    tw = sum(r['n_waves'] for r in results)
    
    avg_ret6 = sum(r['mean_follow_through_6h'] for r in results) / len(results)
    avg_ret12 = sum(r['mean_follow_through_12h'] for r in results) / len(results)
    avg_ret24 = sum(r['mean_follow_through_24h'] for r in results) / len(results)
    
    lines.append(f"\n{'='*62}")
    lines.append("=== SUMMARY ===")
    lines.append(f"{'='*62}")
    lines.append(f"Tickers: {len(results)}  Total waves: {tw}")
    lines.append(f"Avg Acc 6h/12h/24h: {avg6:.1f}% / {avg12:.1f}% / {avg24:.1f}%")
    lines.append(f"Avg Ret 6h/12h/24h: {avg_ret6:+.2f}% / {avg_ret12:+.2f}% / {avg_ret24:+.2f}%")
    lines.append(f"Time: {elapsed:.1f}s")
    
    report = "\n".join(lines)
    print(report)
    
    with open(os.path.join(REPORTS_DIR, 'report.txt'), 'w') as f:
        f.write(report)
    
    json_out = {
        'metadata': {
            'period': f'{START_DATE} to {END_DATE}',
            'method': 'percentile-based yur_ratio',
            'percentile_threshold': PERCENTILE_THRESHOLD,
            'min_wave_hours': MIN_WAVE_HOURS,
            'window': WINDOW,
            'execution_time_seconds': round(elapsed, 1),
            'tickers_total': len(tickers),
            'tickers_with_waves': len(results),
            'total_waves': tw,
        },
        'results': [
            {'symbol': r['symbol'], 'n_waves': r['n_waves'],
             'avg_wave_hours': r['avg_wave_hours'],
             'wave_accuracy_6h': r['wave_accuracy_6h'],
             'wave_accuracy_12h': r['wave_accuracy_12h'],
             'wave_accuracy_24h': r['wave_accuracy_24h'],
             'mean_follow_through_6h': r['mean_follow_through_6h'],
             'mean_follow_through_12h': r['mean_follow_through_12h'],
             'mean_follow_through_24h': r['mean_follow_through_24h'],
             'total_trades_6h': r.get('total_trades_6h', 0),
             'sample_waves': r.get('waves', [])[:10]}
            for r in results
        ],
        'top_tickers': [
            {'symbol': r['symbol'], 'accuracy_6h': r['wave_accuracy_6h'], 'n_waves': r['n_waves']}
            for r in results[:10]
        ],
    }
    
    with open(os.path.join(REPORTS_DIR, 'wave_analysis.json'), 'w') as f:
        json.dump(json_out, f, indent=2, default=str)
    
    print(f"\nSaved: {os.path.join(REPORTS_DIR, 'report.txt')}")
    print(f"Saved: {os.path.join(REPORTS_DIR, 'wave_analysis.json')}")

if __name__ == '__main__':
    main()
