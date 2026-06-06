#!/usr/bin/env python3
"""Crowd Bias — полный скрининг всех 59+ тикеров MOEX.

Тестирует 3 гипотезы на каждом тикере с 5m барами:
1. ANTI_CROWD: против FIZ (FIZ long → short, FIZ short → long)
2. FIZ_FOLLOW: по FIZ
3. YUR_FOLLOW: по YUR
4. DIVERGENCE: когда FIZ и YUR расходятся

No look-ahead. Bar-by-bar. D1 OI → H4 price.
"""

import psycopg2, json, sys, math
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')
MIN_DAYS = 100
MIN_SIGNALS = 10
Z_THRESHOLDS = [0.5, 1.0, 1.5, 2.0]

def get_conn():
    return psycopg2.connect(**DB)

def load_oi_daily(conn, symbol):
    """Load daily OI data."""
    cur = conn.cursor()
    cur.execute("""
        WITH daily AS (
            SELECT time::date as dt,
                MAX(CASE WHEN clgroup=0 THEN buy_orders ELSE 0 END) as fiz_bo,
                MAX(CASE WHEN clgroup=0 THEN sell_orders ELSE 0 END) as fiz_so,
                MAX(CASE WHEN clgroup=1 THEN buy_orders ELSE 0 END) as yur_bo,
                MAX(CASE WHEN clgroup=1 THEN sell_orders ELSE 0 END) as yur_so,
                MAX(CASE WHEN clgroup=0 THEN buy_accounts ELSE 0 END) as fiz_ba,
                MAX(CASE WHEN clgroup=0 THEN sell_accounts ELSE 0 END) as fiz_sa,
                MAX(CASE WHEN clgroup=1 THEN buy_accounts ELSE 0 END) as yur_ba,
                MAX(CASE WHEN clgroup=1 THEN sell_accounts ELSE 0 END) as yur_sa
            FROM openinterest_moex
            WHERE symbol=%s AND buy_accounts>0
            GROUP BY dt
            ORDER BY dt
        )
        SELECT * FROM daily
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    return rows

def load_h4_bars(conn, symbol):
    """Load H4 bars from 5m data."""
    cur = conn.cursor()
    cur.execute("""
        SELECT date_trunc('hour', time) + CASE 
            WHEN EXTRACT(MINUTE FROM time) >= 30 THEN interval '1 hour'
            ELSE interval '0 hours' 
            END as h4_time,
        (array_agg(open ORDER BY time))[1] as open,
        MAX(high) as high,
        MIN(low) as low,
        (array_agg(close ORDER BY time DESC))[1] as close,
        SUM(volume) as volume
        FROM moex_prices_5m
        WHERE symbol=%s
        GROUP BY h4_time
        ORDER BY h4_time
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    return rows

def compute_zscore(series, window=20):
    """z-score без look-ahead: каждое значение относительно прошлого окна"""
    result = [0.0] * len(series)
    for i in range(window, len(series)):
        chunk = series[i-window:i]
        mu = np.mean(chunk)
        sd = np.std(chunk)
        if sd > 0:
            result[i] = (series[i] - mu) / sd
    return result

def test_strategy(bars, oi_map, name, z_threshold, direction_fn, trade_with_fiz_bias=False):
    """
    direction_fn: (fiz_bias, yur_bias, fiz_z, yur_z) -> 'LONG', 'SHORT', or None
    """
    signals = []
    wins = 0
    total = 0
    
    for i in range(1, len(bars)):
        h4_time = bars[i][0]
        dt = h4_time.date()
        
        if dt not in oi_map:
            continue
        
        oi = oi_map[dt]
        fiz_bias = oi['fiz_bias']  # %: positive = net long
        yur_bias = oi['yur_bias']
        fiz_z = oi['fiz_z']
        yur_z = oi['yur_z']
        
        signal = direction_fn(fiz_bias, yur_bias, fiz_z, yur_z)
        if signal is None:
            continue
        
        # Entry: open of current H4 bar (already past, but we use next bar open)
        if i + 1 >= len(bars):
            continue
        entry = bars[i+1][1]  # open of next H4
        
        # Exit: close of 2 bars ahead
        if i + 3 >= len(bars):
            continue
        exit_price = bars[i+3][4]  # close of H4+2
        
        if signal == 'LONG':
            ret = (exit_price - entry) / entry * 100
            win = ret > 0.1  # > 0.1% win
        else:
            ret = (entry - exit_price) / entry * 100
            win = ret > 0.1
        
        signals.append({
            'time': str(h4_time),
            'signal': signal,
            'entry': entry,
            'exit': exit_price,
            'ret': ret,
            'win': win,
            'fiz_bias': fiz_bias,
            'fiz_z': fiz_z,
            'yur_z': yur_z
        })
        total += 1
        if win:
            wins += 1
    
    wr = wins / total * 100 if total > 0 else 0
    returns = [s['ret'] for s in signals]
    pf = sum(r for r in returns if r > 0) / abs(sum(r for r in returns if r < 0)) if any(r < 0 for r in returns) else float('inf')
    
    return {
        'name': name,
        'total': total,
        'wins': wins,
        'wr': round(wr, 1),
        'pf': round(pf, 2) if pf != float('inf') else 'INF',
        'avg_ret': round(np.mean(returns), 2) if returns else 0,
        'signals': signals
    }

def main():
    conn = get_conn()
    
    # Get all symbols with 5m data
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m ORDER BY symbol")
    all_symbols = [r[0] for r in cur.fetchall()]
    cur.close()
    
    print(f"Всего тикеров с 5m данными: {len(all_symbols)}")
    
    results_all = []
    
    for sym in all_symbols:
        print(f"\n{'='*60}")
        print(f"ТИКЕР: {sym}")
        print(f"{'='*60}")
        
        # Load OI
        oi_rows = load_oi_daily(conn, sym)
        if len(oi_rows) < MIN_DAYS:
            print(f"  OI данных мало ({len(oi_rows)} дней, нужно {MIN_DAYS}) — пропуск")
            continue
        
        # Build OI map
        oi_map = {}
        for r in oi_rows:
            dt, fiz_bo, fiz_so, yur_bo, yur_so, fiz_ba, fiz_sa, yur_ba, yur_sa = r
            fiz_bo, fiz_so = float(fiz_bo or 0), float(fiz_so or 0)
            yur_bo, yur_so = float(yur_bo or 0), float(yur_so or 0)
            fiz_ba, fiz_sa = float(fiz_ba or 0), float(fiz_sa or 0)
            
            total_fiz = fiz_ba + fiz_sa
            fiz_bias = (fiz_ba - fiz_sa) / total_fiz * 100 if total_fiz > 0 else 0
            
            total_yur_a = yur_ba + yur_sa if yur_ba else 0
            yur_bias = (yur_bo - yur_so) / (yur_bo + yur_so) * 100 if (yur_bo + yur_so) > 0 else 0
            
            oi_map[dt] = {
                'fiz_bias': fiz_bias,
                'yur_bias': yur_bias,
                'fiz_net': fiz_bo - fiz_so,
                'yur_net': yur_bo - yur_so,
            }
        
        # Compute z-scores
        dates = sorted(oi_map.keys())
        fiz_bias_vals = [oi_map[d]['fiz_bias'] for d in dates]
        yur_bias_vals = [oi_map[d]['yur_bias'] for d in dates]
        
        fiz_z = compute_zscore(fiz_bias_vals, 20)
        yur_z = compute_zscore(yur_bias_vals, 20)
        
        for idx, d in enumerate(dates):
            oi_map[d]['fiz_z'] = fiz_z[idx]
            oi_map[d]['yur_z'] = yur_z[idx]
        
        # Load H4 bars
        bars = load_h4_bars(conn, sym)
        if len(bars) < 100:
            print(f"  H4 баров мало ({len(bars)}) — пропуск")
            continue
        
        print(f"  OI дней: {len(oi_rows)}, H4 баров: {len(bars)}")
        
        # --- Test all hypotheses ---
        for zt in Z_THRESHOLDS:
            # Hypothesis 1: ANTI-CROWD (original idea — trade against FIZ)
            def anti_crowd(fb, yb, fz, yz):
                if abs(fz) < zt:
                    return None
                return 'SHORT' if fz > zt else 'LONG'
            
            r = test_strategy(bars, oi_map, f"ANTI-CROWD|z≥{zt}", zt, anti_crowd)
            if r['total'] >= MIN_SIGNALS:
                print(f"  {r['name']:25s} sig={r['total']:4d} WR={r['wr']:5.1f}% PF={str(r['pf']):6s} avg={r['avg_ret']:+.2f}%")
                results_all.append((sym, r))
            
            # Hypothesis 2: FIZ FOLLOW
            def fiz_follow(fb, yb, fz, yz):
                if abs(fz) < zt:
                    return None
                return 'LONG' if fz > zt else 'SHORT'
            
            r2 = test_strategy(bars, oi_map, f"FIZ-FOLLOW|z≥{zt}", zt, fiz_follow)
            if r2['total'] >= MIN_SIGNALS:
                print(f"  {r2['name']:25s} sig={r2['total']:4d} WR={r2['wr']:5.1f}% PF={str(r2['pf']):6s} avg={r2['avg_ret']:+.2f}%")
                results_all.append((sym, r2))
            
            # Hypothesis 3: YUR FOLLOW — trade with YUR (smart money)
            def yur_follow(fb, yb, fz, yz):
                if abs(yz) < zt:
                    return None
                return 'LONG' if yz > zt else 'SHORT'
            
            r3 = test_strategy(bars, oi_map, f"YUR-FOLLOW|z≥{zt}", zt, yur_follow)
            if r3['total'] >= MIN_SIGNALS:
                print(f"  {r3['name']:25s} sig={r3['total']:4d} WR={r3['wr']:5.1f}% PF={str(r3['pf']):6s} avg={r3['avg_ret']:+.2f}%")
                results_all.append((sym, r3))
            
            # Hypothesis 4: DIVERGENCE — FIZ one way, YUR the other, fade FIZ
            def divergence(fb, yb, fz, yz):
                if abs(fz) < zt or abs(yz) < zt:
                    return None
                if fz > zt and yz < -zt:
                    return 'SHORT'
                if fz < -zt and yz > zt:
                    return 'LONG'
                return None
            
            r4 = test_strategy(bars, oi_map, f"DIVERGENCE|z≥{zt}", zt, divergence)
            if r4['total'] >= MIN_SIGNALS:
                print(f"  {r4['name']:25s} sig={r4['total']:4d} WR={r4['wr']:5.1f}% PF={str(r4['pf']):6s} avg={r4['avg_ret']:+.2f}%")
                results_all.append((sym, r4))
    
    conn.close()
    
    # --- FINAL REPORT ---
    print(f"\n\n{'='*70}")
    print("ИТОГОВЫЙ ОТЧЁТ: Crowd Bias по всем тикерам")
    print(f"{'='*70}")
    print(f"{'Тикер':6s} {'Стратегия':20s} {'Сигн':>5s} {'WR%':>6s} {'PF':>6s} {'AvgRet':>7s}")
    print("-" * 60)
    
    # Sort by WR descending
    results_all.sort(key=lambda x: -x[1]['wr'])
    
    for sym, r in results_all:
        if r['total'] >= MIN_SIGNALS and r['wr'] >= 55:
            print(f"{sym:6s} {r['name']:20s} {r['total']:5d} {r['wr']:5.1f}% {str(r['pf']):>6s} {r['avg_ret']:>+6.2f}%")
    
    # Best anti-crowd tickers
    print(f"\n\n=== ЛУЧШИЕ ANTI-CROWD (оригинальная идея) ===")
    ac_results = [(sym, r) for sym, r in results_all if 'ANTI-CROWD' in r['name'] and r['wr'] >= 55 and r['total'] >= MIN_SIGNALS]
    ac_results.sort(key=lambda x: -x[1]['wr'])
    for sym, r in ac_results[:10]:
        print(f"{sym:6s} {r['name']:25s} sig={r['total']:4d} WR={r['wr']:5.1f}% PF={str(r['pf']):6s} avg={r['avg_ret']:+.2f}%")
    
    # Best YUR follow
    print(f"\n=== ЛУЧШИЕ YUR-FOLLOW (киты) ===")
    yf_results = [(sym, r) for sym, r in results_all if 'YUR-FOLLOW' in r['name'] and r['wr'] >= 55 and r['total'] >= MIN_SIGNALS]
    yf_results.sort(key=lambda x: -x[1]['wr'])
    for sym, r in yf_results[:10]:
        print(f"{sym:6s} {r['name']:25s} sig={r['total']:4d} WR={r['wr']:5.1f}% PF={str(r['pf']):6s} avg={r['avg_ret']:+.2f}%")

if __name__ == '__main__':
    main()
