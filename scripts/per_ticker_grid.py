#!/usr/bin/env python3
"""Per-ticker grid — vectorized.
Precompute scores for all vol_ema variants, then run simulation for each combo."""

import pickle, os, sys, json, time
import numpy as np
import pandas as pd
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
try:
    from scripts.bar_level_sim import TICKER_CONFIGS
except ImportError:
    TICKER_CONFIGS = {}
DEF_GO = 5000

def ema(s, n):
    return s.ewm(span=n, min_periods=n, adjust=False).mean()

def rz(s, w=20):
    m=s.rolling(w,min_periods=w).mean(); std=s.rolling(w,min_periods=w).std()
    return (s-m)/std.clip(lower=1e-10)

def calc_atr(df,p=14):
    prev=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-prev).abs(),(df['low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean().bfill().fillna(0)

TICKER_META = {
    'GL': {'pat':'vod','dir':'L','atm':2,'hold':21}, 'RN': {'pat':'vou','dir':'L','atm':5,'hold':5},
    'AL': {'pat':'vou','dir':'L','atm':2,'hold':21}, 'HY': {'pat':'vou','dir':'L','atm':5,'hold':5},
    'NM': {'pat':'vod','dir':'L','atm':3,'hold':21}, 'AF': {'pat':'sm','dir':'L','atm':2,'hold':21},
    'SR': {'pat':'sm','dir':'L','atm':5,'hold':8},   'Si': {'pat':'vyf','dir':'L','atm':2,'hold':13},
    'SN': {'pat':'vou','dir':'L','atm':5,'hold':5},  'YD': {'pat':'vod','dir':'L','atm':5,'hold':13},
    'BR': {'pat':'vyf','dir':'S','atm':5,'hold':13}, 'SV': {'pat':'vod','dir':'S','atm':5,'hold':5},
    'SF': {'pat':'vod','dir':'S','atm':3,'hold':8},  'NG': {'pat':'vyf','dir':'S','atm':5,'hold':5},
}

VOL_EMAS = [20, 40, 60]
SCORE_EMAS = [0, 12, 20]
THRESHOLDS = [0.30, 0.45, 0.60]
HOLDS = [24, 48, 96]

print("Loading data...", flush=True)
with open('.tf_sweep_data.pkl', 'rb') as f:
    data = pickle.load(f)

results = {}
ts_start = pd.Timestamp('2025-01-01', tz='Asia/Irkutsk')
ts_end = pd.Timestamp('2026-05-01', tz='Asia/Irkutsk')
N_DAYS = 485

for sym, meta in sorted(TICKER_META.items()):
    if sym not in data: continue
    t0 = time.time()
    d = data[sym].copy()
    if len(d) < 100: continue
    
    d['volume'] = d['volume'].astype(float)
    has_oi = 'fiz_buy' in d.columns
    pat, di, atm_base = meta['pat'], meta['dir'], meta['atm']
    dm = 1 if di == 'L' else -1
    
    if has_oi:
        d['fiz_net'] = d['fiz_buy'].fillna(0) - d['fiz_sell'].fillna(0)
        d['yur_net'] = d['yur_buy'].fillna(0) - d['yur_sell'].fillna(0)
        d['fz'] = rz(d['fiz_net'], 20)
        d['yz'] = rz(d['yur_net'], 20)
        d['oi_r'] = (d['yur_buy']+d['yur_sell']).fillna(0) / (d['fiz_buy']+d['fiz_sell']+1).fillna(0)
    
    d['atr14'] = calc_atr(d)
    d['atr_pct'] = d['atr14'] / d['close'].clip(lower=1) * 100
    d['vz'] = rz(d['volume'], 20)
    
    # OI components (common to all vol_ema)
    if has_oi:
        d['oi_r_ema'] = ema(d['oi_r'], 20)
    
    mask = (d.index >= ts_start) & (d.index < ts_end)
    d_test = d[mask]
    n_bars = len(d_test)
    print(f"\n{sym} ({pat},{di}) — {n_bars} bars", end='', flush=True)
    
    # Precompute score arrays for each vol_ema (3 variants)
    score_by_vol = {}
    for vol_n in VOL_EMAS:
        d['vema'] = ema(d['volume'], vol_n)
        d['vr'] = d['volume'] / d['vema'].clip(lower=1)
        vs = np.clip((d['vr'] - 1.5) / 3.0, 0, 1)
        
        if pat in ('vod','vou'):
            if has_oi:
                if pat == 'vod':
                    os_ = np.clip((d['oi_r_ema'] - d['oi_r']) / d['oi_r_ema'].clip(lower=0.1), 0, 1)
                else:
                    os_ = np.clip((d['oi_r'] - d['oi_r_ema']) / d['oi_r_ema'].clip(lower=0.1), 0, 1)
            else: os_ = 0.5
            raw = vs * 0.6 + os_ * 0.4
        elif pat == 'sm':
            if has_oi: raw = np.clip(abs(d['yz'])/3.0,0,1)*0.7 + np.clip(abs(d['fz'])/3.0,0,1)*0.3
            else: raw = np.clip((d['vr']-1.5)/3.0,0,1)
        else:  # vyf
            if has_oi: ys = np.clip(d['yur_net'].fillna(0)/max(d['yur_net'].std(),1)*dm,0,1)
            else: ys = np.clip((d['close']-d['close'].shift(1))/d['close'].shift(1).clip(lower=1)*50,0,1)
            raw = vs * 0.5 + ys * 0.5
        
        af = np.clip(1 - (d['atr_pct'] - 0.3) / 3.0, 0, 1)
        score = np.clip(raw * af * np.clip(1 + d['vz']/5, 0.5, 1.5), 0, 1)
        score_by_vol[vol_n] = score[mask].values.astype(np.float64)
    
    # Prepare arrays for simulation
    close_arr = d_test['close'].values.astype(np.float64)
    high_arr = d_test['high'].values.astype(np.float64)
    low_arr = d_test['low'].values.astype(np.float64)
    atr_arr = d_test['atr14'].values.astype(np.float64)
    
    # Hours mask
    hours = d_test.index.hour.values
    market_hours = (hours >= 7) & (hours < 23)
    
    combos = []
    for vi, vol_n in enumerate(VOL_EMAS):
        score_arr = score_by_vol[vol_n]
        
        for se in SCORE_EMAS:
            if se > 0:
                # Apply EMA to score array
                sc = pd.Series(score_arr).ewm(span=se, min_periods=se, adjust=False).mean().values
            else:
                sc = score_arr
            
            for th in THRESHOLDS:
                for hold in HOLDS:
                    # Fast simulation
                    cash = 100000.0; peak = 100000.0; max_dd = 0.0
                    pos = None; trades = 0
                    
                    i = 0
                    while i < n_bars:
                        if not market_hours[i]:
                            i += 1
                            continue
                        
                        # Exit check
                        if pos is not None:
                            bars = i - pos['entry_i']
                            exit_ep = None
                            
                            if pos['dir'] == 'L' and low_arr[i] <= pos['stop']:
                                exit_ep = pos['stop']
                            elif pos['dir'] == 'S' and high_arr[i] >= pos['stop']:
                                exit_ep = pos['stop']
                            elif bars >= hold:
                                exit_ep = close_arr[i]
                            
                            if exit_ep is not None:
                                pnl_unit = (1 if pos['dir']=='L' else -1) * (exit_ep - pos['entry']) / pos['entry']
                                pnl = pnl_unit * pos['go'] * pos['contracts']
                                cash += pnl
                                trades += 1
                                pos = None
                                # Recalc equity
                                if cash > peak: peak = cash
                                dd = (peak - cash) / peak if peak > 0 else 0
                                if dd > max_dd: max_dd = dd
                        
                        # Entry check
                        if pos is None and market_hours[i] and not np.isnan(sc[i]) and sc[i] >= th:
                            go = TICKER_CONFIGS.get(sym, {}).get('go', DEF_GO)
                            ct = max(1, int(cash * 0.05 / go))
                            if ct > 0 and not np.isnan(atr_arr[i]) and atr_arr[i] > 0:
                                ep = close_arr[i]
                                stop = ep - atr_arr[i] * atm_base if di == 'L' else ep + atr_arr[i] * atm_base
                                cash -= ct * go
                                pos = {'dir': di, 'entry': ep, 'stop': stop, 'contracts': ct, 'go': go, 'entry_i': i}
                        
                        i += 1
                    
                    # Close remaining
                    if pos is not None:
                        cp = close_arr[-1]
                        pnl_unit = (1 if pos['dir']=='L' else -1) * (cp - pos['entry']) / pos['entry']
                        cash += pnl_unit * pos['go'] * pos['contracts']
                        trades += 1
                    
                    ret = (cash - 100000) / 100000 * 100
                    if trades >= 10:
                        calmar = (ret/100) / max_dd if max_dd > 0 else 0
                        combos.append({
                            'vol_ema': vol_n, 'score_ema': se, 'th': th, 'hold': hold,
                            'return_pct': round(ret, 1),
                            'max_dd_pct': round(max_dd*100, 1),
                            'calmar': round(calmar, 2),
                            'n_trades': trades,
                            'tpd': round(trades / N_DAYS, 1),
                        })
    
    combos.sort(key=lambda x: -x['calmar'])
    top5 = combos[:5]
    
    print(f" ({len(combos)} valid combos) {time.time()-t0:.0f}s", flush=True)
    for r in top5:
        print(f"  vema={r['vol_ema']} sema={r['score_ema']} th={r['th']:.2f} hold={r['hold']} → ret={r['return_pct']:+.1f}% DD={r['max_dd_pct']:.1f}% C={r['calmar']:.2f} tr={r['n_trades']} ({r['tpd']}/d)", flush=True)
    
    results[sym] = {'meta': meta, 'top5': top5, 'n_combos': len(combos)}

os.makedirs('reports/tf_sweep', exist_ok=True)
with open('reports/tf_sweep/per_ticker_params.json', 'w') as f:
    json.dump(results, f, indent=2, ensure_ascii=False, default=str)
print(f"\nSaved: reports/tf_sweep/per_ticker_params.json", flush=True)
