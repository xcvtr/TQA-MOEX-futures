#!/usr/bin/env python3
"""OI Wave Analysis — find tickers where OI divergence predicts price movement.
Limited to 30 key tickers for speed."""

import json, os, time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import clickhouse_connect

# ── All MOEX OI tickers ──
TICKERS = [
    'AF', 'AL', 'AU', 'BM', 'BR', 'CC', 'CE', 'CH', 'CNYRUBF', 'CR',
    'DX', 'ED', 'Eu', 'EURRUBF', 'FF', 'GAZPF', 'GD', 'GK', 'GL', 'GLDRUBF',
    'GZ', 'HS', 'HY', 'IB', 'IMOEXF', 'KC', 'LK', 'MC', 'ME', 'MG',
    'MM', 'MN', 'MX', 'MY', 'NA', 'NG', 'NM', 'NR', 'OJ', 'PD',
    'PT', 'RB', 'RI', 'RL', 'RM', 'RN', 'SBERF', 'SE', 'SF', 'Si',
    'SN', 'SP', 'SR', 'SS', 'SV', 'TN', 'TT', 'UC', 'USDRUBF', 'VB',
    'VI', 'W4', 'X5', 'YD',
]

CH_HOST = '127.0.0.1'
CH_PORT = 8123
DAYS = 730  # 2 years

def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

print("Connecting to ClickHouse...", flush=True)
ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT)

results = []
z_scores_all = {}  # For distribution analysis

for sym in TICKERS:
    t0 = time.time()
    print(f"\n{sym}: ", end='', flush=True)
    
    # Load H1 OI from openinterest table (snapshots)
    # We resample 5m prices and OI to H1
    try:
        q = f"""SELECT p.time, p.close, p.high, p.low, p.volume,
                       o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell
                FROM moex.prices_5m p
                LEFT JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
                WHERE p.symbol = '{sym}' AND p.time >= now() - INTERVAL {DAYS} DAY
                ORDER BY p.time"""
        r = ch.query(q)
        if not r.result_rows or len(r.result_rows) < 500:
            print(f"❌ insufficient data ({len(r.result_rows) if r.result_rows else 0} rows)", flush=True)
            continue
        
        cols = ['time', 'close', 'high', 'low', 'volume', 'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell']
        df = pd.DataFrame(r.result_rows, columns=cols)
        df['time'] = pd.to_datetime(df['time'])
        df.set_index('time', inplace=True)
        
        # Resample to H1
        agg = {'close': 'last', 'high': 'max', 'low': 'min', 'volume': 'sum',
               'fiz_buy': 'last', 'fiz_sell': 'last', 'yur_buy': 'last', 'yur_sell': 'last'}
        dh = df.resample('1h').agg(agg).dropna(subset=['close'])
        
        if len(dh) < 100:
            print(f"❌ too few H1 bars ({len(dh)})", flush=True)
            continue
        
        # OI metrics
        dh['fiz_net'] = dh['fiz_buy'].fillna(0) - dh['fiz_sell'].fillna(0)
        dh['yur_net'] = dh['yur_buy'].fillna(0) - dh['yur_sell'].fillna(0)
        dh['oi_ratio'] = (dh['yur_buy'] + dh['yur_sell']).fillna(0) / (dh['fiz_buy'] + dh['fiz_sell'] + 1).fillna(0)
        dh['fiz_z'] = rz(dh['fiz_net'], 20)
        dh['yur_z'] = rz(dh['yur_net'], 20)
        dh['oi_z'] = rz(dh['oi_ratio'], 20)
        
        z_scores_all[sym] = dh['oi_z'].dropna().values.tolist()
        
        # Forward returns
        for h in [6, 12, 24]:
            dh[f'fwd_ret_{h}h'] = dh['close'].shift(-h) / dh['close'] - 1
        
        # Detect OI waves: abs(oi_z) > 2.0 for at least 3 consecutive hours
        dh['wave_signal'] = 0
        dh.loc[dh['oi_z'] > 2.0, 'wave_signal'] = 1    # yur dominant → expect LONG
        dh.loc[dh['oi_z'] < -2.0, 'wave_signal'] = -1   # fiz dominant → expect SHORT
        
        # Find contiguous wave periods
        waves = []
        in_wave = False
        wave_start = None
        wave_dir = 0
        
        for i in range(len(dh)):
            sig = dh['wave_signal'].iloc[i]
            if not in_wave and sig != 0:
                in_wave = True
                wave_start = i
                wave_dir = sig
            elif in_wave and sig == 0:
                # Wave ended — check minimum length (3 hours)
                if i - wave_start >= 3:
                    waves.append((wave_start, i, wave_dir))
                in_wave = False
        # Handle wave at end
        if in_wave and len(dh) - wave_start >= 3:
            waves.append((wave_start, len(dh), wave_dir))
        
        n_waves = len(waves)
        if n_waves == 0:
            print(f"⏹ no waves detected", flush=True)
            results.append({'symbol': sym, 'n_waves': 0, 'avg_hours': 0,
                'acc_6h': 0, 'acc_12h': 0, 'acc_24h': 0,
                'ret_6h': 0, 'ret_12h': 0, 'ret_24h': 0, 'ok': False})
            continue
        
        # Analyze each wave
        wave_hours = []
        correct_6h = 0; correct_12h = 0; correct_24h = 0
        total_6h = 0; total_12h = 0; total_24h = 0
        ret_6h_vals = []; ret_12h_vals = []; ret_24h_vals = []
        
        for ws, we, wdir in waves:
            duration = we - ws
            wave_hours.append(duration)
            
            # Expected direction
            expected_up = (wdir == 1)  # yur dominant → LONG
            
            # Check forward returns at various horizons
            end_idx = we - 1  # last bar of wave
            for h_idx, h in [(6, '6h'), (12, '12h'), (24, '24h')]:
                end_bar = end_idx + h_idx
                if end_bar >= len(dh):
                    continue
                fwd_ret = dh[f'fwd_ret_{h_idx}h'].iloc[end_idx]
                if np.isnan(fwd_ret):
                    continue
                
                if h_idx == 6:
                    total_6h += 1
                    ret_6h_vals.append(fwd_ret)
                    if (fwd_ret > 0) == expected_up:
                        correct_6h += 1
                elif h_idx == 12:
                    total_12h += 1
                    ret_12h_vals.append(fwd_ret)
                    if (fwd_ret > 0) == expected_up:
                        correct_12h += 1
                else:
                    total_24h += 1
                    ret_24h_vals.append(fwd_ret)
                    if (fwd_ret > 0) == expected_up:
                        correct_24h += 1
        
        avg_hours = np.mean(wave_hours) if wave_hours else 0
        acc6 = correct_6h / total_6h * 100 if total_6h > 0 else 0
        acc12 = correct_12h / total_12h * 100 if total_12h > 0 else 0
        acc24 = correct_24h / total_24h * 100 if total_24h > 0 else 0
        r6 = np.mean(ret_6h_vals) * 100 if ret_6h_vals else 0
        r12 = np.mean(ret_12h_vals) * 100 if ret_12h_vals else 0
        r24 = np.mean(ret_24h_vals) * 100 if ret_24h_vals else 0
        
        print(f"✅ {n_waves} waves, avg {avg_hours:.1f}h, acc 6h={acc6:.0f}% 12h={acc12:.0f}% 24h={acc24:.0f}% ({time.time()-t0:.0f}s)", flush=True)
        
        results.append({'symbol': sym, 'n_waves': n_waves, 'avg_hours': round(avg_hours, 1),
            'acc_6h': round(acc6, 1), 'acc_12h': round(acc12, 1), 'acc_24h': round(acc24, 1),
            'ret_6h': round(r6, 2), 'ret_12h': round(r12, 2), 'ret_24h': round(r24, 2),
            'ok': True, 'time_s': round(time.time() - t0, 1)})
    except Exception as e:
        print(f"❌ error: {e}", flush=True)
        results.append({'symbol': sym, 'ok': False, 'error': str(e)})

# ── Print results ──
print(f"\n\n{'='*80}")
print(f"OI WAVE ANALYSIS — H1, |oi_z| > 2.0, >= 3h, {DAYS} days")
print(f"{'='*80}")

valid = [r for r in results if r.get('ok')]
valid.sort(key=lambda x: -x['acc_12h'])

print(f"\n--- TOP 10 by 12h accuracy ---")
print(f"{'#':3} {'Ticker':8} {'Waves':8} {'AvgH':6} {'Acc6h':8} {'Acc12h':8} {'Acc24h':8} {'R6h%':8} {'R12h%':8} {'R24h%':8}")
print(f"{'─'*80}")
for i, r in enumerate(valid[:10]):
    print(f"{i+1:3} {r['symbol']:8} {r['n_waves']:8} {r['avg_hours']:6.1f} {r['acc_6h']:7.1f}% {r['acc_12h']:7.1f}% {r['acc_24h']:7.1f}% {r['ret_6h']:7.2f}% {r['ret_12h']:7.2f}% {r['ret_24h']:7.2f}%")

print(f"\n--- WORST 5 ---")
valid_bad = sorted(valid, key=lambda x: x['acc_12h'])
for i, r in enumerate(valid_bad[:5]):
    print(f"{i+1:3} {r['symbol']:8} {r['n_waves']:8} {r['avg_hours']:6.1f} {r['acc_6h']:7.1f}% {r['acc_12h']:7.1f}% {r['acc_24h']:7.1f}% {r['ret_6h']:7.2f}% {r['ret_12h']:7.2f}% {r['ret_24h']:7.2f}%")

# Count tickers with accuracy > 55% at 12h
good = [r for r in valid if r['acc_12h'] > 55 and r['n_waves'] >= 10]
print(f"\n--- Tickers with Acc12h > 55% and >= 10 waves: {len(good)}/{len(valid)} ---")
for r in sorted(good, key=lambda x: -x['acc_12h']):
    print(f"  {r['symbol']}: Acc12h={r['acc_12h']:.0f}% waves={r['n_waves']} avgH={r['avg_hours']:.0f}h R12h={r['ret_12h']:+.2f}%")

# Wave duration distribution
all_durations = []
for r in valid:
    all_durations.extend([r['avg_hours']] * r['n_waves'])
if all_durations:
    print(f"\n--- Wave duration distribution ---")
    print(f"  Mean: {np.mean(all_durations):.1f}h, Median: {np.median(all_durations):.1f}h")
    for threshold in [3, 6, 12, 24, 48]:
        pct = sum(1 for d in all_durations if d >= threshold) / len(all_durations) * 100
        print(f"  >= {threshold:2d}h: {pct:.0f}%")

# Save
os.makedirs('reports/oi_wave_analysis', exist_ok=True)
with open('reports/oi_wave_analysis/wave_analysis.json', 'w') as f:
    json.dump({'results': results, 'good_tickers': [r['symbol'] for r in good],
               'summary': {'total': len(valid), 'good': len(good)}}, f, indent=2)
print(f"\nSaved: reports/oi_wave_analysis/wave_analysis.json")
print(f"Time: {time.time():.0f}s")
