#!/usr/bin/env python3
"""Analyze which MOEX symbols have tradeable OI waves."""
import sys, os
sys.path.insert(0, os.path.expanduser('~/projects/TQA-MOEX'))
os.chdir(os.path.expanduser('~/projects/TQA-MOEX'))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

TICKERS = ['BR','PD','Si','AF','SR','VB','AL','LK','NM','IMOEXF','Eu','CR']
start, end = '2026-05-11 00:00:00', '2026-05-18 23:50:00'
ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def compute_scores(ticker):
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': start, 'e': end}).result_rows
    if not rows or len(rows) < 50:
        return None
    
    df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'])
    tot = df['total_oi'].values.astype(float)
    tot = np.where(tot <= 0, 1, tot)
    yur_net = (df['yur_buy'].values.astype(float) - df['yur_sell'].values.astype(float)) / tot * 100
    volume = df['volume'].values.astype(float)
    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    
    # z-scores
    s_yur = pd.Series(yur_net)
    yur_z = ((s_yur - s_yur.rolling(40).mean()) / s_yur.rolling(40).std()).fillna(0).values
    s_vol = pd.Series(volume)
    vol_z = ((s_vol - s_vol.rolling(20, min_periods=10).mean()) / s_vol.rolling(20, min_periods=10).std()).fillna(0).values
    
    # ATR
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr_pct = (pd.Series(tr).ewm(span=14).mean() / close * 100).fillna(0).values
    
    # ===== Key metrics =====
    yur_mean = float(yur_net.mean())
    yur_std = float(yur_net.std())
    yur_range = float(yur_net.max() - yur_net.min())
    yur_cv = yur_std / abs(yur_mean) if yur_mean != 0 else 0
    
    # z-score zero crossings = waveform movement
    z_cross = sum(1 for i in range(1, len(yur_z)) if yur_z[i-1]*yur_z[i] < 0)
    z_extreme = sum(1 for z in yur_z if abs(z) > 1.5) / len(yur_z) * 100
    
    # Trade signals (dashboard entry logic)
    atr_th = 1.5
    short_sigs = int(np.sum((vol_z > 3) & (yur_z < -1.5) & (yur_net < 0) & (atr_pct <= atr_th)))
    long_sigs = int(np.sum((vol_z > 3) & (yur_z > 1.5) & (yur_net > 0) & (atr_pct <= atr_th)))
    
    # ===== Wave counts at different thresholds =====
    def count_waves(arr, threshold, min_len=12):
        """Count segments where |arr| > threshold"""
        above = np.abs(arr) > threshold
        waves, i = 0, 0
        while i < len(above):
            if above[i]:
                start = i
                while i < len(above) and above[i]:
                    i += 1
                if i - start >= min_len:
                    waves += 1
            else:
                i += 1
        return waves
    
    # Waves at 15% of yur_range (adaptive threshold)
    thresh15 = yur_range * 0.15
    waves15 = count_waves(yur_net - yur_mean, thresh15, 12)  # deviation from mean
    
    # Waves at 20% threshold of absolute yur_net
    abs_yur = np.abs(yur_net)
    abs_max = float(abs_yur.max())
    waves_abs = count_waves(abs_yur, abs_max * 0.3, 12)
    
    return {
        'ticker': ticker,
        'bars': len(df),
        'yur_mean': round(yur_mean, 1),
        'yur_range': round(yur_range, 1),
        'yur_std': round(yur_std, 2),
        'yur_cv': round(yur_cv, 3),
        'z_cross': z_cross,
        'z_extreme_pct': round(z_extreme, 1),
        'short_sigs': short_sigs,
        'long_sigs': long_sigs,
        'total_sigs': short_sigs + long_sigs,
        'waves15': waves15,
        'sigs_per_day': round((short_sigs + long_sigs) / 7, 1),
        'avg_atr': round(float(atr_pct.mean()), 2),
    }

results = []
for t in TICKERS:
    r = compute_scores(t)
    if r:
        results.append(r)
        sig_str = f"S={r['short_sigs']} L={r['long_sigs']} tot={r['total_sigs']}"
        print(f"{t:>8} | yur={r['yur_mean']:>+5.1f}% rng={r['yur_range']:>5.1f}% cv={r['yur_cv']:>5.3f} | {sig_str:>18} | z_cross={r['z_cross']:>3d} z15={r['z_extreme_pct']:>4.1f}% atr={r['avg_atr']:>4.2f}%")

print()
print("=" * 100)
print("VERDICT — какие символы торговать через OI")
print("=" * 100)

# Scored by: total_sigs * (1 + z_cross/5) * (1 + yur_cv)
scored = []
for r in results:
    score = r['total_sigs'] * (1 + r['z_cross'] / 5) * (1 + r['yur_cv'])
    scored.append((score, r))

scored.sort(key=lambda x: -x[0])
for i, (score, r) in enumerate(scored, 1):
    # Tier logic
    if r['total_sigs'] >= 4 and r['z_cross'] >= 5:
        tier = '✅ ТОРГОВАТЬ'
        note = f"сигналы={r['total_sigs']}/нед, волны OI есть"
    elif r['total_sigs'] >= 2 and r['z_cross'] >= 3:
        tier = '⚠️ УСЛОВНО'
        note = f"сигналов мало ({r['total_sigs']}/нед), тестировать"
    elif r['total_sigs'] >= 4 and r['z_cross'] < 5:
        tier = '⚠️ СИГНАЛЫ ЕСТЬ'
        note = f"но OI не колеблется (z_cross={r['z_cross']})"
    else:
        tier = '❌ НЕТ'
        note = f"сигналов={r['total_sigs']}/нед — OI статичен"
    
    # Add expected profit with hold_max=48
    print(f"{i:>2}. {r['ticker']:>8} | score={score:>6.1f} | {tier:>15} | {note}")

print()
print("Рекомендуемые параметры для Tier 1:")
print("  hold_max=48, exit_yz=0 (отключить), SL=2%, entry как есть (vz>3, yz>1.5, atr<1.5)")
