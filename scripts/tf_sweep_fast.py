#!/usr/bin/env python3
"""Quick TF sweep — считаем количество и частоту сигналов на разных TF.
Без полной портфельной симуляции — только precompute сигналов и подсчёт entry-кандидатов."""

import sys, os, pickle, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bar_level_sim import TICKER_CONFIGS
except ImportError:
    TICKER_CONFIGS = {}

PORTFOLIO = {
    'core': [
        ('GL','vod','L',21,2,1.0), ('RN','vou','L',5,5,1.0),
        ('AL','vou','L',21,2,1.0), ('HY','vou','L',5,5,1.0),
        ('NM','vod','L',21,3,1.0), ('AF','sm','L',21,2,1.0),
        ('SR','sm','L',8,5,1.0),   ('Si','vyf','L',13,2,1.0),
        ('SN','vou','L',5,5,1.0),  ('YD','vod','L',13,5,1.0),
    ],
    'hedge': [
        ('BR','vyf','S',13,5,1.0), ('SV','vod','S',5,5,1.0),
        ('SF','vod','S',8,3,1.0),  ('NG','vyf','S',5,5,1.0),
    ],
}

RULE_MAP = {'5m': '5min', '15m': '15min', 'H1': '1h', 'H4': '4h', 'D1': '1d'}

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

def count_signals(data_5m, tf_rule):
    """Count signals per ticker for this TF. Returns {ticker: count}."""
    signal_counts = {}
    
    for sym in sorted(data_5m.keys()):
        d = data_5m[sym].copy()
        if tf_rule != '5min':
            agg = {'open':'first','high':'max','low':'min','close':'last','volume':'sum',
                   'fiz_buy':'last','fiz_sell':'last','yur_buy':'last','yur_sell':'last'}
            d = d.resample(tf_rule).agg(agg).dropna(subset=['close'])
        if len(d) < 50:
            signal_counts[sym] = 0
            continue
        
        d['volume'] = d['volume'].astype(float)
        d['vma20'] = d['volume'].rolling(20).mean().fillna(d['volume'])
        d['vr'] = d['volume'] / d['vma20'].clip(lower=1)
        d['vz'] = rz(d['volume'], 20)
        has_oi = 'fiz_buy' in d.columns
        if has_oi:
            d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
            d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
            d['fz'] = rz(d['fiz_net'], 20)
            d['yz'] = rz(d['yur_net'], 20)
            d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
            d['oima'] = d['oi_r'].rolling(20).mean()
        d['atr14'] = calc_atr(d)
        d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
        
        # Only test period (2025-01-01 to 2026-04-30)
        mask = (d.index >= '2025-01-01') & (d.index <= '2026-04-30')
        d_test = d[mask]
        if len(d_test) < 10:
            signal_counts[sym] = 0
            continue
        
        # Count how many bars have score above thresholds
        total_entries = 0
        seen = set()
        for lst in PORTFOLIO.values():
            for c in lst:
                sn, pat, di, hold, atm = c[0], c[1], c[2], c[3], c[4]
                if sn != sym: continue
                k = f"{pat}_{di}"
                if k in seen: continue
                seen.add(k)
                
                dm = 1 if di == 'L' else -1
                if pat in ('vod', 'vou'):
                    vs = np.clip((d_test['vr'] - 1.5) / 3.0, 0, 1)
                    if has_oi:
                        if pat == 'vod':
                            os_ = np.clip((d_test['oima'] - d_test['oi_r']) / d_test['oima'].clip(lower=0.1), 0, 1)
                        else:
                            os_ = np.clip((d_test['oi_r'] - d_test['oima']) / d_test['oima'].clip(lower=0.1), 0, 1)
                    else:
                        os_ = 0.5
                    raw = vs * 0.6 + os_ * 0.4
                elif pat == 'sm':
                    if has_oi:
                        raw = np.clip(abs(d_test['yz']) / 3.0, 0, 1) * 0.7 + np.clip(abs(d_test['fz']) / 3.0, 0, 1) * 0.3
                    else:
                        raw = np.clip((d_test['vr'] - 1.5) / 3.0, 0, 1)
                elif pat == 'vyf':
                    vs = np.clip((d_test['vr'] - 2.0) / 4.0, 0, 1)
                    if has_oi:
                        ys = np.clip(d_test['yur_net'].fillna(0) / max(d_test['yur_net'].std(), 1) * dm, 0, 1)
                    else:
                        ys = np.clip((d_test['close'] - d_test['close'].shift(1)) / d_test['close'].shift(1).clip(lower=1) * 50, 0, 1)
                    raw = vs * 0.5 + ys * 0.5
                else:
                    raw = np.clip((d_test['vr'] - 2.5) / 5.0, 0, 1)
                
                af = np.clip(1 - (d_test['atr_pct'] - 0.3) / 3.0, 0, 1)
                score = np.clip(raw * af * np.clip(1 + d_test['vz'] / 5, 0.5, 1.5), 0, 1)
                
                thresh = 0.25 if di == 'L' else 0.20
                above = (score >= thresh).sum()
                total_entries += above
        
        signal_counts[sym] = total_entries
    
    return signal_counts


if __name__ == '__main__':
    print("=" * 70)
    print("TF Sweep — Signal Counts Only (no portfolio simulation)")
    print("=" * 70)
    
    with open('.tf_sweep_data.pkl', 'rb') as f:
        data_5m = pickle.load(f)
    
    all_tfs = ['5m', '15m', 'H1', 'H4']
    days_in_test = 485  # 2025-01-01 to 2026-04-30
    
    results = {}
    for tf_name in all_tfs:
        tf_rule = RULE_MAP[tf_name]
        t0 = time.time()
        print(f"\n--- {tf_name} ---")
        counts = count_signals(data_5m, tf_rule)
        total = sum(counts.values())
        per_day = total / days_in_test
        results[tf_name] = {'total': total, 'per_day': round(per_day, 1), 'by_ticker': counts}
        
        print(f"  Total entries: {total}")
        print(f"  Per day: {per_day:.1f}")
        top = sorted(counts.items(), key=lambda x: -x[1])[:5]
        for sym, cnt in top:
            print(f"    {sym}: {cnt} ({cnt/days_in_test:.1f}/day)")
        print(f"  Time: {time.time()-t0:.1f}s")
    
    print(f"\n{'='*70}")
    print(f"{'TF':6} {'Total entries':>15} {'/day':>8} {'Tickers':>8}")
    print(f"{'─'*70}")
    for tf_name in all_tfs:
        r = results[tf_name]
        active = sum(1 for v in r['by_ticker'].values() if v > 0)
        print(f"{tf_name:6} {r['total']:>15} {r['per_day']:>8.1f} {active:>8}")
    
    # Save
    import json
    os.makedirs('reports/tf_sweep', exist_ok=True)
    summary = {tf: {'total': r['total'], 'per_day': r['per_day']} for tf, r in results.items()}
    with open('reports/tf_sweep/signal_counts.json', 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"\nSaved: reports/tf_sweep/signal_counts.json")
