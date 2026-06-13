#!/usr/bin/env python3
"""Deep OI wave analysis: structure, patterns, and link to price movement."""
import sys, os
sys.path.insert(0, os.path.expanduser('~/projects/TQA-MOEX'))
os.chdir(os.path.expanduser('~/projects/TQA-MOEX'))
import clickhouse_connect
import numpy as np
import pandas as pd
from config import CH_HOST, CH_PORT, CH_DB

ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
TICKERS = ['BR','PD','Si','AF','SR','VB','AL','LK','NM','IMOEXF','Eu','CR']

def load_data(ticker, start='2025-01-01', end='2025-12-31'):
    rows = ch.query("""
        SELECT p.time, p.open, p.high, p.low, p.close, p.volume,
               o.fiz_buy, o.fiz_sell, o.yur_buy, o.yur_sell, o.total_oi
        FROM moex.prices_5m_oi AS o
        INNER JOIN moex.prices_5m AS p ON p.symbol = o.symbol AND p.time = o.time
        WHERE o.symbol = {t:String} AND p.time >= {s:String} AND p.time <= {e:String}
        ORDER BY p.time
    """, parameters={'t': ticker, 's': start, 'e': end}).result_rows
    if not rows or len(rows) < 100: return None
    df = pd.DataFrame(rows, columns=['time','open','high','low','close','volume','fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi'])
    return df

def compute(df):
    tot = df['total_oi'].values.astype(float); tot = np.where(tot <= 0, 1, tot)
    yur_net = (df['yur_buy'] - df['yur_sell']).values.astype(float) / tot * 100
    fiz_net = (df['fiz_buy'] - df['fiz_sell']).values.astype(float) / tot * 100
    volume = df['volume'].values.astype(float)
    close = df['close'].values.astype(float); high = df['high'].values.astype(float); low = df['low'].values.astype(float)
    return yur_net, fiz_net, volume, close, high, low

# ====== WAVE PATTERN ANALYSIS ======
# We need to find: how does yur_net behave BEFORE price moves up/down?
# Hypothesis: price moves when yur_net reaches an extreme and REVERSES

def find_wave_turns(yur_net, lookback=12, min_change=3):
    """Find local extremes of yur_net (wave peaks/troughs).
    A turn is where yur_net changes direction by >= min_change % over lookback bars."""
    n = len(yur_net)
    turns = []
    for i in range(lookback, n - lookback):
        left = yur_net[i-lookback:i]
        right = yur_net[i:i+lookback]
        # Peak: yur_net[i] is higher than both sides
        if yur_net[i] == max(yur_net[i-lookback:i+lookback]) and yur_net[i] > np.mean(left) + min_change:
            turns.append({'idx': i, 'type': 'PEAK', 'val': float(yur_net[i]), 'dir': '→SHORT'})
        # Trough: yur_net[i] is lower than both sides
        elif yur_net[i] == min(yur_net[i-lookback:i+lookback]) and yur_net[i] < np.mean(left) - min_change:
            turns.append({'idx': i, 'type': 'TROUGH', 'val': float(yur_net[i]), 'dir': '→LONG'})
    return turns

def analyze_turn_price_relationship(ticker, df, yur_net, close):
    """For each OI turn, check what price does in the next N bars."""
    n = len(close)
    lookback = 12  # 1h
    min_change = 3  # % change needed
    horizon = 24  # 2h forward
    
    turns = find_wave_turns(yur_net, lookback, min_change)
    
    results = {'peak': [], 'trough': []}
    for t in turns:
        idx = t['idx']
        if idx + horizon >= n: continue
        
        entry_price = close[idx]
        future_prices = close[idx:idx+horizon]
        future_max = float(future_prices.max())
        future_min = float(future_prices.min())
        
        max_move_pct = (future_max / entry_price - 1) * 100
        min_move_pct = (future_min / entry_price - 1) * 100
        end_move_pct = (future_prices[-1] / entry_price - 1) * 100
        
        results[t['type'].lower()].append({
            'val': t['val'],
            'entry': float(entry_price),
            'max_move': round(max_move_pct, 2),
            'min_move': round(min_move_pct, 2),
            'end_move': round(end_move_pct, 2),
            'direction': t['dir'],
        })
    
    return results

print("=" * 100)
print("OI WAVE TURNS vs PRICE MOVEMENT — анализ структуры")
print("=" * 100)

for ticker in TICKERS[:3]:  # BR, PD, Si first
    df = load_data(ticker)
    if df is None: continue
    yur_net, fiz_net, volume, close, high, low = compute(df)
    
    print(f"\n--- {ticker} ---")
    
    for tf in ['5m', 'H1']:
        step = 1 if tf == '5m' else 12
        if step > 1:
            # Resample to H1
            yur_h = pd.Series(yur_net).resample('1h').last().values if False else yur_net[::step][:len(yur_net)//step*step]
            close_h = close[::step][:len(close)//step*step]
            # Simpler: just take every 12th bar
            yur_h = yur_net[::12]
            close_h = close[::12]
            vol_h = volume[::12]
        else:
            yur_h = yur_net
            close_h = close
        
        r = analyze_turn_price_relationship(ticker, df, yur_h, close_h)
        
        print(f"\n  {tf}: {len(r['peak'])} PEAKS + {len(r['trough'])} TROUGHS = {len(r['peak'])+len(r['trough'])} wave turns")
        
        for turn_type in ['peak', 'trough']:
            data = r[turn_type]
            if not data: continue
            
            # Average move after turn
            avg_end = np.mean([d['end_move'] for d in data])
            avg_max = np.mean([d['max_move'] for d in data])
            avg_min = np.mean([d['min_move'] for d in data])
            pct_up = sum(1 for d in data if d['end_move'] > 0) / len(data) * 100
            
            label = 'PEAK (yur max → SHORT)' if turn_type == 'peak' else 'TROUGH (yur min → LONG)'
            print(f"    {label}:")
            print(f"      After turn: avg +{avg_max:.2f}% / {avg_min:.2f}% / end {avg_end:.2f}% | up={pct_up:.0f}%")
            
            # Stratify by OI extreme strength
            strong = [d for d in data if abs(d['val']) > np.mean([abs(x['val']) for x in data])]
            if strong:
                s_end = np.mean([d['end_move'] for d in strong])
                s_up = sum(1 for d in strong if d['end_move'] > 0) / len(strong) * 100
                print(f"      Strong turns (>{abs(np.mean([x['val'] for x in data])):.0f}%): end={s_end:.2f}% up={s_up:.0f}%")

print()
print("=" * 100)
print("ГИСТОГРАММА: после OI PEAK цена идёт ВНИЗ или ВВЕРХ?")
print("=" * 100)

# Focus on BR H1 for detailed analysis
for ticker in ['BR']:
    df = load_data(ticker)
    yur_net, fiz_net, volume, close, high, low = compute(df)
    
    # Take every 12th bar (H1 approximation)
    yur_h, close_h = yur_net[::12], close[::12]
    
    # Find turns
    turns = find_wave_turns(yur_h, lookback=4, min_change=2)  # 4h lookback on H1
    
    print(f"\n{ticker} H1: {len(turns)} wave turns, sample analysis:")
    for t in turns[:8]:
        idx = t['idx']
        if idx + 6 >= len(close_h): continue
        
        # Price 6h after turn
        p0 = close_h[idx]
        p6 = close_h[idx+6]
        move = (p6/p0 - 1) * 100
        
        # yur_net values around turn
        yur_before = yur_h[max(0,idx-4):idx]
        yur_after = yur_h[idx:idx+6]
        
        print(f"  {t['type']:>7} val={t['val']:>+6.1f}% | price {p0:.0f}→{p6:.0f} ({move:>+.2f}%) | yur_before {np.mean(yur_before):+.1f}→{yur_h[idx]:+.1f}→{np.mean(yur_after):+.1f}")

print()
print("=" * 100)
print("КЛЮЧЕВОЙ ТЕСТ: если держать от TROUGH до PEAK и наоборот")
print("=" * 100)

# Simulate: enter LONG at TROUGH, exit at next PEAK (and SHORT: enter at PEAK, exit at next TROUGH)
for ticker in ['BR', 'PD', 'IMOEXF']:
    df = load_data(ticker)
    yur_net, fiz_net, volume, close, high, low = compute(df)
    yur_h, close_h = yur_net[::12], close[::12]
    
    turns = find_wave_turns(yur_h, lookback=4, min_change=2)
    # Sort by idx
    turns.sort(key=lambda x: x['idx'])
    
    trades = []
    for i in range(len(turns)-1):
        t1, t2 = turns[i], turns[i+1]
        if t2['idx'] - t1['idx'] < 2: continue  # skip too close
        
        if t1['type'] == 'TROUGH' and t2['type'] == 'PEAK':
            # LONG from trough to peak
            entry = close_h[t1['idx']]; exit_p = close_h[t2['idx']]
            pnl = (exit_p/entry - 1) * 100 - 0.1  # -spread
            trades.append({'type': 'LONG', 'bars': t2['idx']-t1['idx'], 'pnl': round(pnl, 2)})
        elif t1['type'] == 'PEAK' and t2['type'] == 'TROUGH':
            # SHORT from peak to trough
            entry = close_h[t1['idx']]; exit_p = close_h[t2['idx']]
            pnl = (1 - exit_p/entry) * 100 - 0.1
            trades.append({'type': 'SHORT', 'bars': t2['idx']-t1['idx'], 'pnl': round(pnl, 2)})
    
    if trades:
        pnls = [t['pnl'] for t in trades]
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        avg_bars = np.mean([t['bars'] for t in trades])
        longs = [t for t in trades if t['type'] == 'LONG']
        shorts = [t for t in trades if t['type'] == 'SHORT']
        print(f"\n{ticker}: {len(trades)} wave trades, WR={wr:.0f}% avg_bars={avg_bars:.0f}h")
        if longs:
            l_pnls = [t['pnl'] for t in longs]
            print(f"  LONG: {len(longs)} trades, avg={np.mean(l_pnls):+.2f}% total={sum(l_pnls):+.2f}%")
        if shorts:
            s_pnls = [t['pnl'] for t in shorts]
            print(f"  SHORT: {len(shorts)} trades, avg={np.mean(s_pnls):+.2f}% total={sum(s_pnls):+.2f}%")
