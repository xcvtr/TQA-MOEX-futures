#!/usr/bin/env python3
"""Test OI-wave strategy on single best ticker (GK) with H1 + TP/SL."""

import json, os, sys
import numpy as np
import pandas as pd
import clickhouse_connect

def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-prev).abs(), (df['low']-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)

ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

print("Loading GK data...", flush=True)
q = """SELECT p.time, p.close, p.high, p.low, p.volume,
              o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell
       FROM moex.prices_5m p
       LEFT JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
       WHERE p.symbol = 'GK' AND p.time >= '2024-01-01'
       ORDER BY p.time"""
r = ch.query(q)
cols = ['time', 'close', 'high', 'low', 'volume', 'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell']
df = pd.DataFrame(r.result_rows, columns=cols)
df['time'] = pd.to_datetime(df['time'])
df.set_index('time', inplace=True)

# Resample to H1
agg = {'close': 'last', 'high': 'max', 'low': 'min', 'volume': 'sum',
       'fiz_buy': 'last', 'fiz_sell': 'last', 'yur_buy': 'last', 'yur_sell': 'last'}
dh = df.resample('1h').agg(agg).dropna(subset=['close'])
print(f"  {len(dh)} H1 bars", flush=True)

# OI metrics
dh['oi_ratio'] = (dh['yur_buy'] + dh['yur_sell']).fillna(0) / (dh['fiz_buy'] + dh['fiz_sell'] + 1).fillna(0)
dh['oi_z'] = rz(dh['oi_ratio'], 20)
dh['atr14'] = calc_atr(dh)

# Test period
dh_test = dh['2025-01-01':'2026-06-01']
print(f"  Test: {len(dh_test)} bars", flush=True)

# Grid search
INITIAL = 100000
SLIPPAGE = 0.0001
MIN_WAVE = 3
TIME_STOP = 48
COOLDOWN = 12  # hours

results = []
for tp_m in [1.0, 1.5, 2.0, 2.5, 3.0]:
    for sl_m in [1.0, 1.5, 2.0, 2.5, 3.0]:
        cash = float(INITIAL)
        peak = float(INITIAL)
        max_dd = 0.0
        positions = {}
        trades = []
        pos = None
        pos = None
        cooldown_until = None
        
        for idx in range(len(dh_test)):
            ts = dh_test.index[idx]
            
            # Exit
            if pos is not None:
                bar = dh_test.iloc[idx]
                ep = None; reason = ''
                
                if pos['dir'] == 'L':
                    if bar['high'] >= pos['tp']: ep = pos['tp']; reason = 'tp'
                    elif bar['low'] <= pos['sl']: ep = pos['sl']; reason = 'sl'
                else:
                    if bar['low'] <= pos['tp']: ep = pos['tp']; reason = 'tp'
                    elif bar['high'] >= pos['sl']: ep = pos['sl']; reason = 'sl'
                
                if ep is None and pos.get('bars', 0) >= TIME_STOP:
                    ep = float(bar['close']); reason = 'time'
                
                if ep is not None:
                    pnl = (1 if pos['dir']=='L' else -1) * (ep - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                    cash += pnl
                    trades.append({'pnl': pnl, 'reason': reason})
                    pos = None
                    # Ситуация
                    cooldown_until = idx + COOLDOWN
            
            # MTM
            if pos:
                cp = float(dh_test.iloc[idx]['close'])
                mtm = (1 if pos['dir']=='L' else -1) * (cp - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                teq = cash + mtm
            else:
                teq = cash
            if teq > peak: peak = teq
            dd = (peak - teq) / peak if peak > 0 else 0
            if dd > max_dd: max_dd = dd
            
            # Entry
            if pos is None and (cooldown_until is None or idx >= cooldown_until):
                oi_z = float(dh_test.iloc[idx]['oi_z'])
                if not np.isnan(oi_z) and abs(oi_z) > 2.0:
                    # Check wave: 3 consecutive hours
                    if idx >= 2:
                        prev_ok = True
                        for i in range(idx-2, idx+1):
                            z = dh_test['oi_z'].iloc[i]
                            if np.isnan(z) or abs(z) <= 2.0:
                                prev_ok = False
                                break
                        
                        if prev_ok:
                            direction = 'L' if oi_z > 0 else 'S'
                            go = 5000
                            ct = max(1, int(cash * 0.2 / go))
                            if ct > 0:
                                entry_p = float(dh_test.iloc[idx]['close'])
                                atr_v = float(dh_test.iloc[idx]['atr14'])
                                if not np.isnan(atr_v) and atr_v > 0:
                                    if SLIPPAGE > 0:
                                        entry_p = entry_p * (1 + SLIPPAGE) if direction == 'L' else entry_p * (1 - SLIPPAGE)
                                    
                                    if direction == 'L':
                                        tp = entry_p + atr_v * tp_m
                                        sl = entry_p - atr_v * sl_m
                                    else:
                                        tp = entry_p - atr_v * tp_m
                                        sl = entry_p + atr_v * sl_m
                                    
                                    cost = ct * go
                                    if cost <= cash:
                                        cash -= cost
                                        pos = {'dir': direction, 'entry': entry_p, 'tp': tp, 'sl': sl,
                                               'go': go, 'contracts': ct, 'bars': 0}
            
            # Increment bars
            if pos:
                pos['bars'] += 1
        
        # Close remaining
        if pos:
            ep = float(dh_test.iloc[-1]['close'])
            pnl = (1 if pos['dir']=='L' else -1) * (ep - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
            cash += pnl
            trades.append({'pnl': pnl, 'reason': 'eod'})
        
        ret = (cash - INITIAL) / INITIAL * 100
        n_trades = len(trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        wr = wins / n_trades * 100 if n_trades > 0 else 0
        calmar = (ret/100) / max_dd if max_dd > 0 else 0
        
        results.append({
            'tp_mult': tp_m, 'sl_mult': sl_m,
            'return_pct': round(ret, 2), 'max_dd_pct': round(max_dd*100, 2),
            'calmar': round(calmar, 3), 'wr': round(wr, 1),
            'n_trades': n_trades,
        })

results.sort(key=lambda x: -x['calmar'])
print(f"\n=== GK SINGLE TICKER — TOP 5 ===")
print(f"{'TP':6} {'SL':6} {'Return':10} {'DD':8} {'Calmar':8} {'WR':6} {'Trades':8}")
print(f"{'─'*55}")
for r in results[:5]:
    print(f"{r['tp_mult']:4.1f}x {r['sl_mult']:4.1f}x {r['return_pct']:>+8.2f}% {r['max_dd_pct']:>6.1f}% {r['calmar']:>8.3f} {r['wr']:>5.1f}% {r['n_trades']:>8}")

print(f"\n=== GK SINGLE TICKER — WORST 5 ===")
for r in results[-5:]:
    print(f"{r['tp_mult']:4.1f}x {r['sl_mult']:4.1f}x {r['return_pct']:>+8.2f}% {r['max_dd_pct']:>6.1f}% {r['calmar']:>8.3f} {r['wr']:>5.1f}% {r['n_trades']:>8}")

os.makedirs('reports/oi_wave_strategy', exist_ok=True)
with open('reports/oi_wave_strategy/gk_single.json', 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved: reports/oi_wave_strategy/gk_single.json")
