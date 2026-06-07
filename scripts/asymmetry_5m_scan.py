#!/usr/bin/env python3
"""5m FIZ/YUR asymmetry scanner — NO D1 data, pure 5m resolution."""

import psycopg2, sys
from datetime import datetime
import numpy as np

DB = dict(host='10.0.0.60', port=5432, dbname='moex', user='postgres', password='postgres')

def zs(vals, w=20):
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x - mu)**2 for x in chunk) / w
        sd = var ** 0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out

def analyze_5m(symbol):
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
        FROM moex_prices_5m_oi
        WHERE symbol=%s AND time >= '2023-01-01'
        ORDER BY time
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if len(rows) < 1000:
        return None
    
    n = len(rows)
    
    # FIZ/YUR net per 5m bar
    fiz_net = np.array([float(r[1] or 0) - float(r[2] or 0) for r in rows])
    yur_net = np.array([float(r[3] or 0) - float(r[4] or 0) for r in rows])
    total_oi = np.array([float(r[5] or 0) for r in rows])
    
    # Volume per group
    fiz_vol = np.array([float(r[1] or 0) + float(r[2] or 0) for r in rows])
    yur_vol = np.array([float(r[3] or 0) + float(r[4] or 0) for r in rows])
    
    # 1. STRUCTURAL ASYMMETRY: who has more volume?
    avg_fiz_vol = np.mean(fiz_vol)
    avg_yur_vol = np.mean(yur_vol)
    yur_fiz_vol_ratio = avg_yur_vol / avg_fiz_vol if avg_fiz_vol > 0 else 99
    
    # 2. DIRECTIONAL ALIGNMENT: how often do FIZ and YUR go same direction?
    same_dir = np.sum((fiz_net > 0) & (yur_net > 0)) + np.sum((fiz_net < 0) & (yur_net < 0))
    opp_dir = np.sum((fiz_net > 0) & (yur_net < 0)) + np.sum((fiz_net < 0) & (yur_net > 0))
    same_dir_pct = same_dir / (same_dir + opp_dir) * 100 if (same_dir + opp_dir) > 0 else 0
    
    # 3. FIZ DOMINANCE: which direction does FIZ lean on average?
    fiz_bias_mean = np.mean(fiz_net)
    yur_bias_mean = np.mean(yur_net)
    
    # 4. FIZ EXTREME EVENTS: days where FIZ is strongly directional
    fiz_z = zs(fiz_net.tolist(), 20)
    extreme_count = sum(1 for z in fiz_z if abs(z) > 2.0)
    extreme_pct = extreme_count / n * 100
    
    yur_z = zs(yur_net.tolist(), 20)
    yur_extreme = sum(1 for z in yur_z if abs(z) > 2.0)
    yur_extreme_pct = yur_extreme / n * 100
    
    # 5. DIVERGENCE EVENTS: FIZ one way, YUR the other (z-score based)
    div_events = 0
    for i in range(20, n):
        if abs(fiz_z[i]) > 1.5 and abs(yur_z[i]) > 1.5 and fiz_z[i] * yur_z[i] < 0:
            div_events += 1
    div_pct = div_events / n * 100
    
    # 6. WHO WINS: when they diverge, which direction wins?
    # (we need price data for this - skip here, do in strategy test)
    
    # 7. Asymmetry score: composite
    # Higher = more asymmetric market structure
    asym_vol = max(yur_fiz_vol_ratio, 1.0/yur_fiz_vol_ratio) if yur_fiz_vol_ratio > 0 else 1
    asym_dir = 50 - abs(same_dir_pct - 50)  # how far from 50/50
    asym_extreme = max(extreme_pct, yur_extreme_pct)
    
    # Liquidity proxy: average OI level
    avg_oi = np.mean(total_oi)
    if avg_oi > 500000:
        liq = 'HIGH'
    elif avg_oi > 30000:
        liq = 'MEDIUM'
    elif avg_oi > 5000:
        liq = 'LOW'
    else:
        liq = 'MICRO'
    
    return {
        'symbol': symbol,
        'rows': n,
        'avg_oi': round(avg_oi, 0),
        'liq': liq,
        'fiz_vol': round(avg_fiz_vol, 0),
        'yur_vol': round(avg_yur_vol, 0),
        'yur_fiz_ratio': round(yur_fiz_vol_ratio, 2),
        'same_dir': round(same_dir_pct, 1),
        'opp_dir': round(100 - same_dir_pct, 1),
        'fiz_bias': round(fiz_bias_mean, 1),
        'yur_bias': round(yur_bias_mean, 1),
        'fiz_extreme_pct': round(extreme_pct, 2),
        'yur_extreme_pct': round(yur_extreme_pct, 2),
        'div_pct': round(div_pct, 2),
        'asym_vol': round(asym_vol, 2),
    }

def main():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m_oi ORDER BY symbol")
    all_symbols = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    
    results = []
    for i, sym in enumerate(all_symbols):
        print(f"[{i+1}/{len(all_symbols)}] {sym}...", end=' ', flush=True)
        r = analyze_5m(sym)
        if r:
            results.append(r)
            dir_info = "⬆" if r['same_dir'] > 55 else ("⬇" if r['opp_dir'] > 55 else "↕")
            print(f"OI={r['avg_oi']:.0f} {r['liq']} "
                  f"FIZ/YUR={r['yur_fiz_ratio']:.2f} "
                  f"Dir={r['same_dir']:5.1f}/{r['opp_dir']:5.1f}% {dir_info} "
                  f"Div={r['div_pct']:.2f}% "
                  f"Extr={r['fiz_extreme_pct']:.2f}/{r['yur_extreme_pct']:.2f}%")
        else:
            print("SKIP")
    
    print(f"\n\n{'='*130}")
    print("5m FIZ/YUR АСИММЕТРИЯ — ВСЕ ТИКЕРЫ")
    print(f"{'='*130}")
    print(f"{'Тикер':>5} | {'OI':>8} | {'Ликв':>5} | {'YUR/FIZ':>7} | "
          f"{'Вместе%':>7} | {'Против%':>7} | {'FIZbias':>7} | {'YURbias':>7} | "
          f"{'FIZ>2σ%':>8} | {'YUR>2σ%':>8} | {'Div%':>6} | {'Асим':>5}")
    print("-" * 130)
    
    # Sort by divergence frequency (high = lots of FIZ/YUR fights)
    results.sort(key=lambda x: -x['div_pct'])
    for r in results:
        print(f"{r['symbol']:>5} | {r['avg_oi']:>8.0f} | {r['liq']:>5} | "
              f"{r['yur_fiz_ratio']:>7.2f} | {r['same_dir']:>6.1f}% | "
              f"{r['opp_dir']:>6.1f}% | {r['fiz_bias']:>+7.1f} | {r['yur_bias']:>+7.1f} | "
              f"{r['fiz_extreme_pct']:>7.2f}% | {r['yur_extreme_pct']:>7.2f}% | "
              f"{r['div_pct']:>5.2f}% | {r['asym_vol']:>5.2f}")
    
    # SWEET SPOT: MEDIUM liq + lots of divergence + asymmetric
    print(f"\n\n{'='*130}")
    print("SWEET SPOT: MEDIUM liq + частые дивергенции + асимметрия")
    print(f"{'='*130}")
    
    sweet = [r for r in results 
             if r['liq'] in ('MEDIUM', 'LOW') 
             and r['div_pct'] > 2.0
             and r['asym_vol'] > 1.5
             and r['fiz_extreme_pct'] > 3.0]
    sweet.sort(key=lambda x: -x['div_pct'])
    
    if sweet:
        print(f"{'Тикер':>5} | {'OI':>8} | {'Ликв':>5} | {'YUR/FIZ':>7} | "
              f"{'Вместе%':>7} | {'Против%':>7} | {'Div%':>6} | {'Асим':>5}")
        print("-" * 65)
        for r in sweet:
            print(f"{r['symbol']:>5} | {r['avg_oi']:>8.0f} | {r['liq']:>5} | "
                  f"{r['yur_fiz_ratio']:>7.2f} | {r['same_dir']:>6.1f}% | "
                  f"{r['opp_dir']:>6.1f}% | {r['div_pct']:>5.2f}% | {r['asym_vol']:>5.2f}")
    else:
        print("Ничего в sweet spot. Снимаю фильтры...")
        sweet2 = [r for r in results if r['liq'] != 'HIGH' and r['div_pct'] > 1.5]
        sweet2.sort(key=lambda x: -x['div_pct'])
        for r in sweet2[:10]:
            print(f"{r['symbol']:>5} | {r['avg_oi']:>8.0f} | {r['liq']:>5} | "
                  f"{r['yur_fiz_ratio']:>7.2f} | {r['same_dir']:>6.1f}% | "
                  f"{r['opp_dir']:>6.1f}% | {r['div_pct']:>5.2f}% | {r['asym_vol']:>5.2f}")

if __name__ == '__main__':
    main()
