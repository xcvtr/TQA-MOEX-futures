#!/usr/bin/env python3
"""TRIZ-based exit: yur_net mean reversion instead of z-score threshold."""
import sys, os
sys.path.insert(0, os.path.expanduser('~/projects/TQA-MOEX'))
os.chdir(os.path.expanduser('~/projects/TQA-MOEX'))
import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

TICKERS = ['BR','PD','Si','AF','SR','VB','AL','LK','NM','IMOEXF','Eu','CR']
ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

def simulate(ticker, hold_max=48, exit_yz=0, yur_exit_sigma=1.0):
    """yur_exit_sigma: exit when yur_net returns to rolling mean +/- k*std"""
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': '2025-01-01 00:00:00', 'e': '2025-12-31 23:50:00'}).result_rows
    if not rows or len(rows) < 100: return None
    
    df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'])
    tot = df['total_oi'].values.astype(float); tot = np.where(tot <= 0, 1, tot)
    yur_net = (df['yur_buy'] - df['yur_sell']).values.astype(float) / tot * 100
    volume = df['volume'].values.astype(float)
    close = df['close'].values.astype(float); high = df['high'].values.astype(float); low = df['low'].values.astype(float)
    open_px = df['open'].values.astype(float)
    n = len(yur_net)
    
    # Indicators (dashboard-compatible)
    s_vol = pd.Series(volume)
    vol_z = ((s_vol - s_vol.rolling(20,min_periods=10).mean()) / s_vol.rolling(20,min_periods=10).std()).fillna(0).values
    s_yur = pd.Series(yur_net)
    yur_z = ((s_yur - s_yur.rolling(40).mean()) / s_yur.rolling(40).std()).fillna(0).values
    tr = np.maximum(high-low, np.maximum(np.abs(high-np.roll(close,1)), np.abs(low-np.roll(close,1))))
    tr[0] = high[0]-low[0]
    atr_pct = (pd.Series(tr).ewm(span=14).mean() / close * 100).fillna(0).values
    
    # Rolling mean/std of yur_net (40-bar window = ~3.3h)
    yur_mean_r = pd.Series(yur_net).rolling(40, min_periods=10).mean().bfill().values
    yur_std_r = pd.Series(yur_net).rolling(40, min_periods=10).std().bfill().values
    yur_std_r = np.where(yur_std_r < 0.5, 0.5, yur_std_r)
    
    sig_mask = (vol_z > 3) & (np.abs(yur_z) > 1.5) & (atr_pct <= 1.5)
    sig_indices = np.where(sig_mask)[0]
    
    trades = []
    for idx in sig_indices:
        if idx + 1 >= n: continue
        direction = 1 if yur_net[idx] > 0 else -1
        if (direction == 1 and yur_net[idx] <= 0) or (direction == -1 and yur_net[idx] >= 0): continue
        entry = float(open_px[idx+1])
        if entry <= 0: continue
        
        # Find exit
        off = hold_max
        for o in range(1, min(hold_max + 1, n - idx - 1)):
            exit_now = False
            if exit_yz > 0 and abs(yur_z[idx + o]) < exit_yz:
                exit_now = True
            if yur_exit_sigma and direction == -1:
                # SHORT: exit when yur_net rises TO mean+sigma (weakening to mean)
                if yur_net[idx + o] >= yur_mean_r[idx + o] + yur_exit_sigma * yur_std_r[idx + o]:
                    exit_now = True
            if yur_exit_sigma and direction == 1:
                # LONG: exit when yur_net drops TO mean-sigma
                if yur_net[idx + o] <= yur_mean_r[idx + o] - yur_exit_sigma * yur_std_r[idx + o]:
                    exit_now = True
            if exit_now:
                off = o
                break
        
        exit_idx = idx + off
        if exit_idx >= n: continue
        exit_px = float(close[exit_idx])
        hit_stop = False
        if direction == 1:
            sl = entry * 0.98
            for j in range(idx+1, exit_idx+1):
                if low[j] <= sl: exit_px = sl; hit_stop = True; break
            pnl = (exit_px - entry) / 0.01 - 2
        else:
            sl = entry * 1.02
            for j in range(idx+1, exit_idx+1):
                if high[j] >= sl: exit_px = sl; hit_stop = True; break
            pnl = (entry - exit_px) / 0.01 - 2
        
        trades.append({'pnl': round(pnl,2), 'bars': off, 'dir': direction})
    return trades

# Test all tickers with 3 strategies
strategies = [
    ('CURRENT (yz=0.5/h24)', 24, 0.5, None),
    ('H48 only', 48, 0, None),
    ('TRIZ mean+1σ/h48', 48, 0, 1.0),
    ('TRIZ mean+0.5σ/h48', 48, 0, 0.5),
    ('TRIZ mean+1.5σ/h48', 48, 0, 1.5),
]

print(f"{'Ticker':>8}", end='')
for name,_,_,_ in strategies:
    print(f" | {name:>22}", end='')
print()
print('-' * 150)

for t in TICKERS:
    print(f"{t:>8}", end='')
    for name, hm, eyz, ysig in strategies:
        trades = simulate(t, hm, eyz, ysig)
        if trades:
            pnls = [x['pnl'] for x in trades]; bars = [x['bars'] for x in trades]
            wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            long_n = sum(1 for x in trades if x['dir'] == 1)
            print(f" | {len(trades):>3d}tr {np.mean(bars):>5.0f}b {sum(pnls):>+7.0f}p {wr:>4.0f}%", end='')
        else:
            print(f" | {'—':>22}", end='')
    print()
