#!/usr/bin/env python3
"""Full 2025 OI wave analysis: find symbols with tradeable OI dynamics."""
import sys, os
sys.path.insert(0, os.path.expanduser('~/projects/TQA-MOEX'))
os.chdir(os.path.expanduser('~/projects/TQA-MOEX'))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

TICKERS = ['BR','PD','Si','AF','SR','VB','AL','LK','NM','IMOEXF','Eu','CR']
START = '2025-01-01 00:00:00'
END = '2025-12-31 23:50:00'
ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def analyze_symbol(ticker):
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': START, 'e': END}).result_rows
    if not rows or len(rows) < 1000:
        return None
    
    df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'])
    tot = df['total_oi'].values.astype(float); tot = np.where(tot <= 0, 1, tot)
    yur_net = (df['yur_buy'].values.astype(float) - df['yur_sell'].values.astype(float)) / tot * 100
    fiz_net = (df['fiz_buy'].values.astype(float) - df['fiz_sell'].values.astype(float)) / tot * 100
    volume = df['volume'].values.astype(float)
    close = df['close'].values.astype(float); high = df['high'].values.astype(float); low = df['low'].values.astype(float)
    
    n = len(yur_net)
    n_days = n / (24 * 12)  # 5m bars per day
    
    # === 1. Yur_net statistics ===
    yur_mean = float(yur_net.mean())
    yur_std = float(yur_net.std())
    yur_min = float(yur_net.min())
    yur_max = float(yur_net.max())
    yur_range = yur_max - yur_min
    yur_cv = yur_std / abs(yur_mean) if yur_mean != 0 else 99
    
    # === 2. z-score movement (waveform quality) ===
    s = pd.Series(yur_net)
    yur_z = ((s - s.rolling(40).mean()) / s.rolling(40).std()).fillna(0).values
    
    z_cross = sum(1 for i in range(1, n) if yur_z[i-1]*yur_z[i] < 0)
    z_extreme_pct = sum(1 for z in yur_z if abs(z) > 1.5) / n * 100
    
    # === 3. Signal counts (dashboard entry logic) ===
    s_vol = pd.Series(volume)
    vol_z = ((s_vol - s_vol.rolling(20, min_periods=10).mean()) / s_vol.rolling(20, min_periods=10).std()).fillna(0).values
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr_pct = (pd.Series(tr).ewm(span=14).mean() / close * 100).fillna(0).values
    
    short_sigs = int(np.sum((vol_z > 3) & (yur_z < -1.5) & (yur_net < 0) & (atr_pct <= 1.5)))
    long_sigs = int(np.sum((vol_z > 3) & (yur_z > 1.5) & (yur_net > 0) & (atr_pct <= 1.5)))
    
    # === 4. Yur_net swings — how many times does it cross mean±half_range ===
    half_range = yur_range * 0.25
    above = yur_net > yur_mean + half_range
    below = yur_net < yur_mean - half_range
    swings = 0
    in_swing = False
    for i in range(n):
        if above[i] or below[i]:
            if not in_swing:
                swings += 1
                in_swing = True
        else:
            in_swing = False
    
    # === 5. Yur_net sign changes (LONG opportunities) ===
    sign_changes = sum(1 for i in range(1, n) if yur_net[i-1]*yur_net[i] < 0)
    pct_positive = sum(1 for v in yur_net if v > 0) / n * 100
    
    return {
        'ticker': ticker, 'bars': n, 'days': round(n_days, 0),
        'yur_mean': round(yur_mean, 1), 'yur_std': round(yur_std, 2),
        'yur_range': round(yur_range, 1), 'yur_cv': round(yur_cv, 3),
        'yur_min': round(yur_min, 1), 'yur_max': round(yur_max, 1),
        'z_cross': z_cross, 'z_cross_day': round(z_cross / n_days, 1),
        'z_extreme_pct': round(z_extreme_pct, 1),
        'short_sigs': short_sigs, 'long_sigs': long_sigs,
        'total_sigs': short_sigs + long_sigs,
        'sigs_day': round((short_sigs + long_sigs) / n_days, 1),
        'swings': swings, 'swings_day': round(swings / n_days, 1),
        'sign_changes': sign_changes,
        'pct_pos': round(pct_positive, 1),
    }

# Row data storage
all_results = []

print(f"{'='*140}")
print(f"{'OI WAVE ANALYSIS — 2025 FULL YEAR':^140}")
print(f"{'='*140}")
print(f"{'Ticker':>8} {'Bars':>7} {'Days':>5} | {'YurMn':>7} {'YurRng':>7} {'CV':>6} {'Zcrs/d':>7} {'Z15%':>6} | {'Sigs':>5} {'L':>4} {'S':>4} {'/day':>5} | {'Swng':>5} {'/day':>5} | {'SignΔ':>6} {'Pos%':>6} | {'VERDICT':>12}")
print(f"{'-'*140}")

for t in TICKERS:
    r = analyze_symbol(t)
    if not r: continue
    all_results.append(r)
    
    # Score: want z_cross_per_day >= 0.5 AND sigs_per_day >= 0.1 AND yur_cv > 0.05
    sig_score = r['sigs_day']
    wave_score = r['z_cross_day']
    cv_score = r['yur_cv']
    
    # Tier
    if r['total_sigs'] >= 10 and r['z_cross_day'] >= 0.5 and r['yur_cv'] > 0.05:
        tier = '✅ TOP'
    elif r['total_sigs'] >= 5 and r['z_cross_day'] >= 0.3:
        tier = '⚠️ OK'
    elif r['total_sigs'] >= 2:
        tier = '❌ POOR'
    else:
        tier = '⛔ DEAD'
    
    pct_pos_str = f"{r['pct_pos']:.1f}%"
    if r['pct_pos'] > 1:
        pct_pos_str += ' ⚠️LONG!'
    
    print(f"{r['ticker']:>8} {r['bars']:>7d} {r['days']:>5.0f} | {r['yur_mean']:>+6.1f}% {r['yur_range']:>6.1f}% {r['yur_cv']:>5.3f} | {r['z_cross_day']:>5.1f} {r['z_extreme_pct']:>5.1f}% | {r['total_sigs']:>4d} {r['long_sigs']:>4d} {r['short_sigs']:>4d} {r['sigs_day']:>4.2f} | {r['swings']:>5d} {r['swings_day']:>4.2f} | {r['sign_changes']:>5d} {pct_pos_str:>8} | {tier:>12}")

print()
print(f"{'='*140}")
print("DETAILED ANALYSIS")
print()

# Detailed per-ticker breakdown of the best candidates
print("--- СИМВОЛЫ С СИЛЬНЫМИ OI ВОЛНАМИ ---")
print()
for r in all_results:
    if r['total_sigs'] < 10 or r['z_cross_day'] < 0.3: continue
    print(f"{r['ticker']}:")
    print(f"  Yur_net: {r['yur_mean']:+.1f}% (min={r['yur_min']:+.1f}% max={r['yur_max']:+.1f}% range={r['yur_range']:.1f}%)")
    print(f"  Волатильность OI: CV={r['yur_cv']:.3f}, z-crossings={r['z_cross']} ({r['z_cross_day']:.1f}/день)")
    print(f"  Сигналы: {r['total_sigs']} ({r['sigs_day']:.2f}/день) — {r['short_sigs']} short / {r['long_sigs']} long")
    print(f"  Yur_net знак меняется {r['sign_changes']} раз, позитивных={r['pct_pos']:.1f}%")
    print(f"  Swings (>25% размаха): {r['swings']} ({r['swings_day']:.2f}/день)")
    print()

print("--- СИМВОЛЫ БЕЗ OI ВОЛН ---")
for r in all_results:
    if r['total_sigs'] >= 10 and r['z_cross_day'] >= 0.3: continue
    print(f"  {r['ticker']}: sigs={r['total_sigs']} ({r['sigs_day']:.2f}/д) z_cross={r['z_cross_day']:.1f}/д cv={r['yur_cv']:.3f} range={r['yur_range']:.1f}%")
