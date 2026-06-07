#!/usr/bin/env python3
"""Scan all MOEX tickers for FIZ/YUR asymmetry — find markets with one manipulator + crowd."""

import psycopg2, json, sys
from datetime import datetime
import numpy as np

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')

# All tickers with 5m OI data
TICKERS = [
    'AF','AL','BM','CC','CE','DX','GK','GL','HS','HY','IB','KC','MC','ME','MG',
    'MM','NG','NM','NR','OJ','PD','PT','RN','SE','SF','SN','SP','SS','SV','TN','TT','VB','W4','YD'
]

def get_daily_oi_stats(conn, symbol):
    """Get daily FIZ/YUR stats: OI levels, asymmetry, dominance."""
    cur = conn.cursor()
    cur.execute("""
        SELECT time::date as dt,
            -- FIZ
            MAX(CASE WHEN clgroup=0 THEN buy_orders ELSE 0 END) as fiz_buy,
            MAX(CASE WHEN clgroup=0 THEN sell_orders ELSE 0 END) as fiz_sell,
            MAX(CASE WHEN clgroup=0 THEN buy_accounts ELSE 0 END) as fiz_buy_acc,
            MAX(CASE WHEN clgroup=0 THEN sell_accounts ELSE 0 END) as fiz_sell_acc,
            -- YUR
            MAX(CASE WHEN clgroup=1 THEN buy_orders ELSE 0 END) as yur_buy,
            MAX(CASE WHEN clgroup=1 THEN sell_orders ELSE 0 END) as yur_sell,
            MAX(CASE WHEN clgroup=1 THEN buy_accounts ELSE 0 END) as yur_buy_acc,
            MAX(CASE WHEN clgroup=1 THEN sell_accounts ELSE 0 END) as yur_sell_acc
        FROM openinterest_moex
        WHERE symbol=%s AND time >= '2023-01-01'
        GROUP BY dt
        ORDER BY dt
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    
    if len(rows) < 100:
        return None
    
    # Build time series
    dates = []
    fiz_net_pct = []   # FIZ net bias as % of total accounts
    yur_oi_total = []  # YUR total OI (liquidity indicator)
    fiz_oi_total = []  # FIZ total OI
    
    for r in rows:
        dt, fb, fs, fba, fsa, yb, ys, yba, ysa = r
        fb, fs = float(fb or 0), float(fs or 0)
        yb, ys = float(yb or 0), float(ys or 0)
        fba, fsa = float(fba or 0), float(fsa or 0)
        
        fiz_oi = fb + fs
        yur_oi = yb + ys
        total_acc = fba + fsa
        
        if total_acc == 0:
            continue
        
        fiz_bias = (fba - fsa) / total_acc * 100  # + = net long
        
        dates.append(dt)
        fiz_net_pct.append(fiz_bias)
        yur_oi_total.append(yur_oi)
        fiz_oi_total.append(fiz_oi)
    
    n = len(dates)
    if n < 100:
        return None
    
    # Core metrics
    avg_fiz_oi = np.mean(fiz_oi_total)
    avg_yur_oi = np.mean(yur_oi_total)
    total_oi = avg_fiz_oi + avg_yur_oi
    
    # FIZ bias statistics
    fiz_bias_mean = np.mean(fiz_net_pct)
    fiz_bias_std = np.std(fiz_net_pct)
    fiz_bias_extremes = sum(1 for v in fiz_net_pct if abs(v) > 20)  # extreme days
    
    # YUR dominance: days where YUR > FIZ by 2x
    yur_dom_days = 0
    fiz_dom_days = 0
    balanced_days = 0
    for i in range(n):
        if yur_oi_total[i] > fiz_oi_total[i] * 2:
            yur_dom_days += 1
        elif fiz_oi_total[i] > yur_oi_total[i] * 2:
            fiz_dom_days += 1
        else:
            balanced_days += 1
    
    # FIZ position extremes (z-score based, rolling window)
    fiz_z = [0.0] * n
    window = 20
    for i in range(window, n):
        chunk = fiz_net_pct[i-window:i]
        mu = np.mean(chunk)
        sd = np.std(chunk)
        if sd > 0:
            fiz_z[i] = (fiz_net_pct[i] - mu) / sd
    
    # Count divergence events: |fiz_z| > 1.5 and YUR OI moving opposite direction
    # Simplified: count extreme FIZ days
    fiz_extreme_pct = sum(1 for z in fiz_z if abs(z) > 2.0) / n * 100
    fiz_moderate = sum(1 for z in fiz_z if abs(z) > 1.5) / n * 100
    
    # YUR/FIZ ratio (who dominates the market)
    if avg_fiz_oi > 0:
        yur_fiz_ratio = avg_yur_oi / avg_fiz_oi
    else:
        yur_fiz_ratio = float('inf')
    
    # Liquidity tier
    if total_oi > 100000:
        liq_tier = 'HIGH'
    elif total_oi > 10000:
        liq_tier = 'MEDIUM'
    elif total_oi > 1000:
        liq_tier = 'LOW'
    else:
        liq_tier = 'MICRO'
    
    # Asymmetry score: higher = one group dominates more
    # Based on: yur_fiz_ratio deviation from 1.0 + fiz_bias_std + frequency of extreme
    if yur_fiz_ratio > 1:
        asym_score = yur_fiz_ratio
    else:
        asym_score = 1.0 / yur_fiz_ratio if yur_fiz_ratio > 0 else 99
    
    return {
        'symbol': symbol,
        'days': n,
        'avg_fiz_oi': round(avg_fiz_oi, 0),
        'avg_yur_oi': round(avg_yur_oi, 0),
        'total_oi': round(total_oi, 0),
        'liq_tier': liq_tier,
        'yur_fiz_ratio': round(yur_fiz_ratio, 2),
        'fiz_bias_mean': round(fiz_bias_mean, 2),
        'fiz_bias_std': round(fiz_bias_std, 2),
        'fiz_extreme_pct': round(fiz_extreme_pct, 1),
        'fiz_moderate_pct': round(fiz_moderate, 1),
        'yur_dom_days_pct': round(yur_dom_days / n * 100, 1),
        'fiz_dom_days_pct': round(fiz_dom_days / n * 100, 1),
        'balanced_pct': round(balanced_days / n * 100, 1),
        'asymmetry_score': round(asym_score, 2),
    }

def main():
    conn = psycopg2.connect(**DB)
    results = []
    
    for i, sym in enumerate(TICKERS):
        print(f"[{i+1}/{len(TICKERS)}] {sym}...", end=' ', flush=True)
        r = get_daily_oi_stats(conn, sym)
        if r:
            results.append(r)
            print(f"OI={r['total_oi']:.0f} FIZ/YUR={r['yur_fiz_ratio']:.1f} "
                  f"FIZ_std={r['fiz_bias_std']:.1f}% "
                  f"Extreme={r['fiz_extreme_pct']:.1f}% "
                  f"Dom={r['yur_dom_days_pct']:.0f}/{r['fiz_dom_days_pct']:.0f}/{r['balanced_pct']:.0f}%")
        else:
            print("SKIP")
    
    conn.close()
    
    print(f"\n\n{'='*110}")
    print("FIZ/YUR АСИММЕТРИЯ — ВСЕ ТИКЕРЫ")
    print(f"{'='*110}")
    print(f"{'Тикер':>5} | {'OI':>7} | {'Ликв':>6} | {'YUR/FIZ':>7} | {'FIZ±σ':>6} | "
          f"{'FIZ>2σ':>7} | {'YURдоми':>6} | {'FIZдоми':>6} | {'Баланс':>6} | {'Асимм':>6}")
    print("-" * 110)
    results.sort(key=lambda x: -x['total_oi'])
    for r in results:
        print(f"{r['symbol']:>5} | {r['total_oi']:>7.0f} | {r['liq_tier']:>6} | "
              f"{r['yur_fiz_ratio']:>7.2f} | {r['fiz_bias_std']:>5.1f}% | "
              f"{r['fiz_extreme_pct']:>6.1f}% | {r['yur_dom_days_pct']:>5.1f}% | "
              f"{r['fiz_dom_days_pct']:>5.1f}% | {r['balanced_pct']:>5.1f}% | "
              f"{r['asymmetry_score']:>6.2f}")
    
    # SWEET SPOT: MEDIUM liquidity + asymmetry
    print(f"\n\n{'='*110}")
    print("ЗОЛОТАЯ СЕРЕДИНА (MEDIUM ликвидность + асимметрия)")
    print(f"{'='*110}")
    sweet = [r for r in results if r['liq_tier'] == 'MEDIUM' and r['asymmetry_score'] > 1.5]
    sweet.sort(key=lambda x: -x['asymmetry_score'])
    if sweet:
        print(f"{'Тикер':>5} | {'OI':>7} | {'YUR/FIZ':>7} | {'FIZ±σ':>6} | "
              f"{'FIZ>2σ':>7} | {'YURдоми':>6} | {'FIZдоми':>6} | {'Баланс':>6} | {'Асимм':>6}")
        print("-" * 80)
        for r in sweet:
            print(f"{r['symbol']:>5} | {r['total_oi']:>7.0f} | {r['yur_fiz_ratio']:>7.2f} | "
                  f"{r['fiz_bias_std']:>5.1f}% | {r['fiz_extreme_pct']:>6.1f}% | "
                  f"{r['yur_dom_days_pct']:>5.1f}% | {r['fiz_dom_days_pct']:>5.1f}% | "
                  f"{r['balanced_pct']:>5.1f}% | {r['asymmetry_score']:>6.2f}")
    else:
        print("MEDIUM — нет. Смотрим LOW.")
        low_sweet = [r for r in results if r['liq_tier'] == 'LOW' and r['asymmetry_score'] > 1.5]
        low_sweet.sort(key=lambda x: -x['asymmetry_score'])
        for r in low_sweet:
            print(f"{r['symbol']:>5} | {r['total_oi']:>7.0f} | {r['yur_fiz_ratio']:>7.2f} | "
                  f"{r['fiz_bias_std']:>5.1f}% | {r['fiz_extreme_pct']:>6.1f}% | "
                  f"{r['yur_dom_days_pct']:>5.1f}% | {r['fiz_dom_days_pct']:>5.1f}% | "
                  f"{r['balanced_pct']:>5.1f}% | {r['asymmetry_score']:>6.2f}")

if __name__ == '__main__':
    main()
