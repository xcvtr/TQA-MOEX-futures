#!/usr/bin/env python3
"""
OI Trade Analysis — сравнение exit-стратегий для всех тикеров.
Импортирует compute_indicators из moex_oi_dashboard.py.
"""
import sys, os, json
sys.path.insert(0, os.path.expanduser('~/projects/TQA-MOEX'))
os.chdir(os.path.expanduser('~/projects/TQA-MOEX'))

import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

# Replicate the resample_oi and compute_indicators logic
def load_df(ticker, start='2026-05-11 00:00:00', end='2026-05-18 23:50:00', tf='5m'):
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': start, 'e': end}).result_rows
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        'time','open','high','low','close','volume',
        'fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'
    ])
    if tf != '5m':
        rule = {'15m': '15min', 'H1': '1h'}.get(tf, '5min')
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time').resample(rule).agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
            'volume': 'sum', 'fiz_buy': 'last', 'fiz_sell': 'last',
            'yur_buy': 'last', 'yur_sell': 'last', 'total_oi': 'last'
        }).dropna().reset_index()
    return df

def compute_indicators_raw(df):
    """Returns the arrays, not just the JSON-safe dict."""
    closes = df['close'].values.astype(float)
    fiz_buy = df['fiz_buy'].values.astype(float)
    fiz_sell = df['fiz_sell'].values.astype(float)
    yur_buy = df['yur_buy'].values.astype(float)
    yur_sell = df['yur_sell'].values.astype(float)
    total_oi = df['total_oi'].values.astype(float)
    total_oi = np.where(total_oi <= 0, 1, total_oi)
    
    fiz_long = fiz_buy / total_oi * 100
    fiz_short = fiz_sell / total_oi * 100
    yur_long = yur_buy / total_oi * 100
    yur_short = yur_sell / total_oi * 100
    fiz_net = (fiz_buy - fiz_sell) / total_oi * 100
    yur_net = (yur_buy - yur_sell) / total_oi * 100
    crowd_share = (fiz_buy + fiz_sell) / total_oi * 100
    fiz_long_premium = (fiz_buy - fiz_sell) / (fiz_buy + fiz_sell + 1) * 100
    
    # z-scores
    W = min(40, len(fiz_net) - 1)
    s = pd.Series(fiz_net)
    z_fiz = ((s - s.rolling(W).mean()) / s.rolling(W).std()).fillna(0).values
    
    vol_s = pd.Series(df['volume'].values.astype(float))
    vol_mu = vol_s.rolling(20, min_periods=10).mean()
    vol_sd = vol_s.rolling(20, min_periods=10).std().fillna(1).replace(0, 1)
    vol_z = ((vol_s - vol_mu) / vol_sd).fillna(0).values
    
    yur_s = pd.Series(yur_net)
    yn_mu = yur_s.rolling(20, min_periods=10).mean()
    yn_sd = yur_s.rolling(20, min_periods=10).std().fillna(1).replace(0, 1)
    yur_z = ((yur_s - yn_mu) / yn_sd).fillna(0).values
    
    # ATR
    atr_pct = np.zeros(len(closes))
    if len(closes) > 15:
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(closes, 1)), np.abs(low - np.roll(closes, 1))))
        tr[0] = high[0] - low[0]
        atr_series = pd.Series(tr).ewm(span=14).mean()
        atr_pct = (atr_series / closes * 100).fillna(0).values
    
    return {
        'closes': closes, 'open': df['open'].values.astype(float),
        'high': df['high'].values.astype(float), 'low': df['low'].values.astype(float),
        'yur_net': yur_net, 'yur_z': yur_z, 'vol_z': vol_z, 'z_fiz': z_fiz,
        'atr_pct': atr_pct, 'times': [str(t)[:19] for t in df['time']],
    }

def simulate(d, vz_th=3.0, yz_th=1.5, atr_th=1.5, hold_max=24, exit_yz=0.5, sl_pct=0.02, exit_yur_decay=0):
    """Simulate trades with given parameters. Returns list of trades."""
    n = len(d['closes'])
    trades = []
    long_mask = (d['vol_z'] > vz_th) & (d['yur_z'] > yz_th) & (d['yur_net'] > 0) & (d['atr_pct'] <= atr_th)
    short_mask = (d['vol_z'] > vz_th) & (d['yur_z'] < -yz_th) & (d['yur_net'] < 0) & (d['atr_pct'] <= atr_th)
    
    for sig_indices, direction in [(np.where(long_mask)[0], 1), (np.where(short_mask)[0], -1)]:
        for idx in sig_indices:
            if idx + 1 >= n: continue
            entry = float(d['open'][idx+1])
            if entry <= 0: continue
            
            entry_yur = d['yur_net'][idx]
            off = hold_max
            for o in range(1, min(hold_max + 1, n - idx - 1)):
                exit_now = False
                if exit_yz > 0 and abs(d['yur_z'][idx + o]) < exit_yz:
                    exit_now = True
                if exit_yur_decay > 0 and direction == -1:
                    if d['yur_net'][idx + o] > entry_yur + exit_yur_decay:
                        exit_now = True
                if exit_yur_decay > 0 and direction == 1:
                    if d['yur_net'][idx + o] < entry_yur - exit_yur_decay:
                        exit_now = True
                if exit_now:
                    off = o
                    break
            
            exit_idx = idx + off
            if exit_idx >= n: continue
            exit_px = float(d['closes'][exit_idx])
            
            hit_stop = False
            if direction == 1:
                stop_level = entry * (1 - sl_pct)
                for j in range(idx + 1, exit_idx + 1):
                    if d['low'][j] <= stop_level:
                        exit_px = stop_level; hit_stop = True; break
                pnl = (exit_px - entry) / 0.01 - 2
            else:
                stop_level = entry * (1 + sl_pct)
                for j in range(idx + 1, exit_idx + 1):
                    if d['high'][j] >= stop_level:
                        exit_px = stop_level; hit_stop = True; break
                pnl = (entry - exit_px) / 0.01 - 2
            
            trades.append({'pnl': round(pnl, 2), 'bars': off, 'sl': hit_stop, 'dir': direction})
    return trades

# Load all tickers
TICKERS = ['BR', 'PD', 'Si', 'AF', 'SR', 'VB', 'AL', 'LK', 'NM', 'IMOEXF', 'Eu', 'CR']
start, end = '2026-05-11 00:00:00', '2026-05-18 23:50:00'

strategies = [
    # name, exit_yz, hold_max, exit_yur_decay
    ('CURRENT: yz=0.5/h24', 0.5, 24, 0),
    ('yz=0.5/h48', 0.5, 48, 0),
    ('yz=0.5/h96', 0.5, 96, 0),
    ('only h48', 0, 48, 0),
    ('only h96', 0, 96, 0),
    ('ydecay=3/h48', 0, 48, 3),
    ('ydecay=3/h96', 0, 96, 3),
    ('ydecay=5/h96', 0, 96, 5),
]

for ticker in TICKERS:
    df = load_df(ticker, start, end)
    if df is None or len(df) < 20:
        print(f"{ticker:8s}: no data ({len(df) if df is not None else 0} rows)")
        continue
    d = compute_indicators_raw(df)
    print(f"\n{'='*70}")
    print(f"{ticker:8s} | {len(d['closes'])} bars | yur_net={d['yur_net'].mean():+.1f}±{d['yur_net'].std():.1f}%")
    print(f"{'Strategy':40s} | {'Trades':>7} | {'AvgBars':>7} | {'AvgPnl':>8} | {'Total':>8} | {'WR':>6}")
    print('-' * 80)
    
    for name, eyz, hm, ydec in strategies:
        trades = simulate(d, exit_yz=eyz, hold_max=hm, exit_yur_decay=ydec)
        if trades:
            pnls = [t['pnl'] for t in trades]
            bars = [t['bars'] for t in trades]
            wr = len([p for p in pnls if p > 0]) / len(pnls) * 100
            print(f"{name:40s} | {len(trades):>7d} | {np.mean(bars):>6.0f}b | {np.mean(pnls):>+7.0f}p | {sum(pnls):>+7.0f}p | {wr:>5.0f}%")
        else:
            print(f"{name:40s} | {'—':>7} | {'—':>7} | {'—':>8} | {'—':>8} | {'—':>6}")
