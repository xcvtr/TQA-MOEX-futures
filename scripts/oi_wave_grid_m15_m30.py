#!/usr/bin/env python3
"""OI Wave TP/SL grid — M15 and M30 in one run.
Optimized: precompute once, simulate for each combo."""

import json, os, sys, time
from collections import defaultdict

import numpy as np
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

TICKERS = ['GK', 'AF', 'MG', 'YD', 'SR', 'NR']

def ema(s, n):
    return s.ewm(span=n, min_periods=n, adjust=False).mean()

def rz(s, w=20):
    m = s.rolling(w, min_periods=w).mean()
    std = s.rolling(w, min_periods=w).std()
    return (s - m) / std.clip(lower=1e-10)

def calc_atr(df, p=14):
    prev = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-prev).abs(), (df['low']-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=p).mean().bfill().fillna(0)

# ── TF configs ──
TF_CONFIGS = {
    'M30': {'rule': '30min', 'min_wave': 3, 'time_stop': 48, 'cooldown_hours': 6, 'z_window': 20},
}
TP_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]
SL_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]
INITIAL_CAPITAL = 100000
SLIPPAGE = 0.0001
N_DAYS = 485

print("Connecting to ClickHouse...", flush=True)
ch = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

for tf_name, tfc in TF_CONFIGS.items():
    print(f"\n{'='*60}", flush=True)
    print(f"TF: {tf_name} (rule={tfc['rule']})", flush=True)
    print(f"{'='*60}", flush=True)
    t_start = time.time()
    
    # Load & resample all tickers
    all_data = {}
    for sym in TICKERS:
        try:
            q = f"""SELECT p.time, p.close, p.high, p.low, p.volume,
                           o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell
                    FROM moex.prices_5m p
                    LEFT JOIN moex.prices_5m_oi o ON p.time = o.time AND p.symbol = o.symbol
                    WHERE p.symbol = '{sym}' AND p.time >= '2024-01-01'
                    ORDER BY p.time"""
            r = ch.query(q)
            if not r.result_rows or len(r.result_rows) < 500:
                print(f"  {sym}: insufficient data", flush=True)
                continue
            cols = ['time', 'close', 'high', 'low', 'volume', 'fiz_buy', 'fiz_sell', 'yur_buy', 'yur_sell']
            df = pd.DataFrame(r.result_rows, columns=cols)
            df['time'] = pd.to_datetime(df['time'])
            df.set_index('time', inplace=True)
            
            # Resample
            agg = {'close': 'last', 'high': 'max', 'low': 'min', 'volume': 'sum',
                   'fiz_buy': 'last', 'fiz_sell': 'last', 'yur_buy': 'last', 'yur_sell': 'last'}
            dh = df.resample(tfc['rule']).agg(agg).dropna(subset=['close'])
            
            if len(dh) < 200:
                print(f"  {sym}: too few bars ({len(dh)})", flush=True)
                continue
            
            # OI metrics
            dh['fiz_net'] = dh['fiz_buy'].fillna(0) - dh['fiz_sell'].fillna(0)
            dh['yur_net'] = dh['yur_buy'].fillna(0) - dh['yur_sell'].fillna(0)
            dh['oi_ratio'] = (dh['yur_buy'] + dh['yur_sell']).fillna(0) / (dh['fiz_buy'] + dh['fiz_sell'] + 1).fillna(0)
            dh['oi_z'] = rz(dh['oi_ratio'], tfc['z_window'])
            dh['atr14'] = calc_atr(dh)
            
            all_data[sym] = dh
            print(f"  {sym}: {len(dh)} bars", flush=True)
        except Exception as e:
            print(f"  {sym}: error {e}", flush=True)
    
    if not all_data:
        print("  No data loaded, skipping", flush=True)
        continue
    
    ts_start = pd.Timestamp('2025-01-01', tz='Asia/Irkutsk')
    ts_end = pd.Timestamp('2026-06-01', tz='Asia/Irkutsk')
    
    results = []
    
    for tp_m in TP_MULTS:
        for sl_m in SL_MULTS:
            cash = float(INITIAL_CAPITAL)
            peak = float(INITIAL_CAPITAL)
            max_dd = 0.0
            positions = {}
            trades = []
            cooldown = {}  # sym -> time when cooldown ends
            
            # Build combined time index from all tickers
            all_ts = set()
            for sym, dh in all_data.items():
                for t in dh.index:
                    if ts_start <= t < ts_end:
                        all_ts.add(t)
            all_ts = sorted(all_ts)
            
            for ts in all_ts:
                # Exits
                for sym in list(positions.keys()):
                    pos = positions[sym]
                    if sym not in all_data or ts not in all_data[sym].index:
                        continue
                    bar = all_data[sym].loc[ts]
                    ep = None; reason = ''
                    
                    if pos['dir'] == 'L':
                        if bar['high'] >= pos['tp']:
                            ep = pos['tp']; reason = 'tp'
                        elif bar['low'] <= pos['sl']:
                            ep = pos['sl']; reason = 'sl'
                    else:  # S
                        if bar['low'] <= pos['tp']:
                            ep = pos['tp']; reason = 'tp'
                        elif bar['high'] >= pos['sl']:
                            ep = pos['sl']; reason = 'sl'
                    
                    if ep is None and pos.get('bars', 0) >= tfc['time_stop']:
                        ep = float(bar['close']); reason = 'time'
                    
                    if ep is not None:
                        pnl = (1 if pos['dir']=='L' else -1) * (ep - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                        cash += pnl
                        trades.append({'pnl': pnl, 'reason': reason, 'sym': sym})
                        del positions[sym]
                        cooldown[sym] = ts + pd.Timedelta(hours=tfc['cooldown_hours'])
                
                # MTM
                mtm = 0
                for sym, pos in positions.items():
                    if sym in all_data and ts in all_data[sym].index:
                        cp = float(all_data[sym].loc[ts, 'close'])
                        mtm += (1 if pos['dir']=='L' else -1) * (cp - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                teq = cash + mtm
                if teq > peak: peak = teq
                dd = (peak - teq) / peak if peak > 0 else 0
                if dd > max_dd: max_dd = dd
                
                # Entries — check each ticker for wave start
                if len(positions) >= 3:
                    continue
                
                for sym, dh in all_data.items():
                    if sym in positions:
                        continue
                    if sym in cooldown and ts < cooldown[sym]:
                        continue
                    if ts not in dh.index:
                        continue
                    
                    oi_z = float(dh.loc[ts, 'oi_z'])
                    if np.isnan(oi_z):
                        continue
                    
                    # Check for wave start: |oi_z| > 2.0 for min_wave bars
                    idx = dh.index.get_loc(ts)
                    if idx < tfc['min_wave'] - 1:
                        continue
                    
                    is_wave = True
                    for i in range(idx - tfc['min_wave'] + 1, idx + 1):
                        if abs(dh['oi_z'].iloc[i]) <= 2.0 or np.isnan(dh['oi_z'].iloc[i]):
                            is_wave = False
                            break
                    
                    if not is_wave:
                        continue
                    
                    # Determine direction
                    direction = 'L' if oi_z > 0 else 'S'
                    
                    # Entry
                    go = 5000
                    ct = max(1, int(cash * 0.15 / go))
                    if ct == 0: continue
                    
                    # Price and ATR at entry
                    bar = dh.loc[ts]
                    entry_p = float(bar['close'])
                    atr_v = float(bar['atr14'])
                    if np.isnan(atr_v) or atr_v <= 0: continue
                    
                    if direction == 'L':
                        tp = entry_p + atr_v * tp_m
                        sl = entry_p - atr_v * sl_m
                    else:
                        tp = entry_p - atr_v * tp_m
                        sl = entry_p + atr_v * sl_m
                    
                    # Apply slippage
                    if SLIPPAGE > 0:
                        entry_p = entry_p * (1 + SLIPPAGE) if direction == 'L' else entry_p * (1 - SLIPPAGE)
                    
                    cost = ct * go
                    if cost > cash: continue
                    cash -= cost
                    
                    positions[sym] = {'dir': direction, 'entry': entry_p, 'tp': tp, 'sl': sl,
                                      'go': go, 'contracts': ct, 'bars': 0}
                
                # Increment bars held
                for sym in positions:
                    positions[sym]['bars'] = positions[sym].get('bars', 0) + 1
            
            # Close remaining
            for sym in list(positions.keys()):
                pos = positions[sym]
                if sym in all_data:
                    lb = all_data[sym].iloc[-1]
                    ep = float(lb['close'])
                    pnl = (1 if pos['dir']=='L' else -1) * (ep - pos['entry']) / pos['entry'] * pos['go'] * pos['contracts']
                    cash += pnl
                    trades.append({'pnl': pnl, 'reason': 'eod', 'sym': sym})
            
            ret = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
            n_trades = len(trades)
            wins = sum(1 for t in trades if t['pnl'] > 0)
            wr = wins / n_trades * 100 if n_trades > 0 else 0
            calmar = (ret/100) / max_dd if max_dd > 0 else 0
            tpd = n_trades / N_DAYS if N_DAYS > 0 else 0
            
            results.append({
                'tp_mult': tp_m, 'sl_mult': sl_m,
                'return_pct': round(ret, 2), 'max_dd_pct': round(max_dd*100, 2),
                'calmar': round(calmar, 3), 'wr': round(wr, 1),
                'n_trades': n_trades, 'trades_per_day': round(tpd, 2),
            })
            
            if len(results) % 5 == 0:
                print(f"  {len(results)}/25 combos done ({time.time()-t_start:.0f}s)", flush=True)
    
    results.sort(key=lambda x: -x['calmar'])
    
    print(f"\n--- TOP 5 for {tf_name} ---", flush=True)
    print(f"{'TP':6} {'SL':6} {'Return':10} {'DD':8} {'Calmar':8} {'WR':6} {'Trades':8} {'/day':6}")
    print(f"{'─'*60}")
    for r in results[:5]:
        print(f"{r['tp_mult']:4.1f}x {r['sl_mult']:4.1f}x {r['return_pct']:>+8.2f}% {r['max_dd_pct']:>6.1f}% {r['calmar']:>8.3f} {r['wr']:>5.1f}% {r['n_trades']:>8} {r['trades_per_day']:>5.2f}")
    
    print(f"\n--- WORST 5 for {tf_name} ---", flush=True)
    for r in results[-5:]:
        print(f"{r['tp_mult']:4.1f}x {r['sl_mult']:4.1f}x {r['return_pct']:>+8.2f}% {r['max_dd_pct']:>6.1f}% {r['calmar']:>8.3f} {r['wr']:>5.1f}% {r['n_trades']:>8} {r['trades_per_day']:>5.2f}")
    
    os.makedirs('reports/oi_wave_strategy', exist_ok=True)
    with open(f'reports/oi_wave_strategy/tp_sl_grid_{tf_name.lower()}.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"  Time: {time.time()-t_start:.0f}s", flush=True)

print(f"\n{'='*60}", flush=True)
print("ALL DONE", flush=True)
