"""
Optimized VC TRIZ analysis: run on top 6 major symbols + 2 min symbols.
Skip sequential ADX (too slow), focus on key metrics.
"""
import sys
sys.path.insert(0, '/home/user/projects/TQA-crypto')
import numpy as np
import datetime
from engine.db import get_conn

def compute_volume_zscore(volume, window=100):
    """Rolling z-score — only past data."""
    z = np.full(len(volume), np.nan)
    vol_sum = 0.0
    vol_sum_sq = 0.0
    # Warmup window
    for i in range(window):
        if i >= len(volume):
            break
    # Rolling with deque-like approach
    from collections import deque
    buf = deque(maxlen=window)
    for i in range(len(volume)):
        buf.append(volume[i])
        if len(buf) < window:
            continue
        mean = sum(buf) / window
        std = (sum((v - mean)**2 for v in buf) / (window - 1))**0.5
        if std > 0:
            z[i] = (volume[i] - mean) / std
        else:
            z[i] = 0.0
    return z

def compute_atr_sequential(high, low, close, period=14):
    """ATR — sequential, no look-ahead."""
    n = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i-1])
        lc = abs(low[i] - close[i-1])
        tr[i] = max(hl, hc, lc)
    atr = np.full(n, np.nan)
    for i in range(period, n):
        atr[i] = np.mean(tr[i-period+1:i+1])
    return atr, tr

def compute_sma_trend(close, period=50):
    """Simple trend strength: distance from SMA(50) as %."""
    n = len(close)
    trend = np.full(n, np.nan)
    for i in range(period, n):
        sma = np.mean(close[i-period:i])
        trend[i] = (close[i] - sma) / sma * 100  # % distance from SMA
    return trend

def compute_bb_pos(close, period=20):
    """BB position: -1 to +1."""
    n = len(close)
    bb = np.full(n, np.nan)
    for i in range(period, n):
        w = close[i-period+1:i+1]
        m = np.mean(w)
        s = np.std(w, ddof=1)
        if s > 0:
            bb[i] = (close[i] - (m-2*s)) / (4*s) * 2 - 1
    return bb

def compute_short_adx(high, low, close, period=14):
    """Fast ADX computation using vectorized primitives."""
    n = len(close)
    up = np.zeros(n)
    down = np.zeros(n)
    tr = np.zeros(n)
    
    for i in range(1, n):
        up[i] = max(high[i] - high[i-1], 0)
        down[i] = max(low[i-1] - low[i], 0)
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    
    pdi = np.full(n, np.nan)
    mdi = np.full(n, np.nan)
    dx = np.full(n, np.nan)
    adx = np.full(n, np.nan)
    
    for i in range(period, n):
        atr_i = np.mean(tr[i-period+1:i+1])
        if atr_i > 0:
            pdi[i] = 100 * np.mean(up[i-period+1:i+1]) / atr_i
            mdi[i] = 100 * np.mean(down[i-period+1:i+1]) / atr_i
            di_sum = pdi[i] + mdi[i]
            if di_sum > 0:
                dx[i] = 100 * abs(pdi[i] - mdi[i]) / di_sum
    
    for i in range(period*2, n):
        adx[i] = np.mean(dx[i-period+1:i+1])
    
    return adx

def compute_rsi(close, period=14):
    """RSI — sequential."""
    n = len(close)
    rsi = np.full(n, np.nan)
    gains = np.zeros(n)
    losses = np.zeros(n)
    for i in range(1, n):
        diff = close[i] - close[i-1]
        gains[i] = max(diff, 0)
        losses[i] = max(-diff, 0)
    for i in range(period, n):
        avg_g = np.mean(gains[i-period+1:i+1])
        avg_l = np.mean(losses[i-period+1:i+1])
        if avg_l > 0:
            rsi[i] = 100 - 100 / (1 + avg_g/avg_l)
        else:
            rsi[i] = 100
    return rsi

def run_fast_vc(conn, symbols):
    """Fast VC analysis — only z_threshold=3.0, limited indicators."""
    all_results = []
    
    for sym in symbols:
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines "
            "WHERE exchange='binance' AND symbol=%s AND interval='5m' "
            "ORDER BY timestamp",
            (sym,)
        ).fetchall()
        
        if not rows or len(rows) < 200:
            print(f"  {sym}: SKIP ({len(rows) if rows else 0} rows)")
            continue
        
        # Parse timestamps
        r0 = rows[0]
        if isinstance(r0['timestamp'], datetime.datetime):
            ts_num = np.array([int(t.timestamp()) for t in [r['timestamp'] for r in rows]])
        elif r0['timestamp'] > 1e11:
            ts_num = np.array([r['timestamp'] // 1000 for r in rows])
        else:
            ts_num = np.array([r['timestamp'] for r in rows])
        
        close = np.array([r['close'] for r in rows], dtype=np.float64)
        high = np.array([r['high'] for r in rows], dtype=np.float64)
        low = np.array([r['low'] for r in rows], dtype=np.float64)
        volume = np.array([r['volume'] for r in rows], dtype=np.float64)
        
        n = len(close)
        print(f"  {sym}: {n} candles ({datetime.datetime.fromtimestamp(int(ts_num[0]))} .. {datetime.datetime.fromtimestamp(int(ts_num[-1]))})")
        
        z = compute_volume_zscore(volume, 100)
        bb_pos = compute_bb_pos(close, 20)
        trend_dist = compute_sma_trend(close, 50)
        rsi = compute_rsi(close, 14)
        
        # Try multiple z thresholds
        for zt in [2.5, 3.0, 3.5]:
            vc_idx = np.where(z > zt)[0]
            
            for idx in vc_idx:
                entry_idx = idx - 1
                if entry_idx < 0 or idx < 100 or idx >= n - 12:
                    continue
                
                exit_idx = min(idx + 12, n - 1)
                
                entry_price = close[entry_idx]
                exit_price = close[exit_idx]
                
                vc_open = rows[idx]['open']
                vc_close = close[idx]
                vc_green = vc_close > vc_open
                
                # FADE: trade against VC candle
                if vc_green:
                    move_fade = ((entry_price - exit_price) / entry_price) * 100
                    win_fade = exit_price < entry_price
                else:
                    move_fade = ((exit_price - entry_price) / entry_price) * 100
                    win_fade = exit_price > entry_price
                
                # RIDE: trade with VC candle
                if vc_green:
                    move_ride = ((exit_price - entry_price) / entry_price) * 100
                    win_ride = exit_price > entry_price
                else:
                    move_ride = ((entry_price - exit_price) / entry_price) * 100
                    win_ride = exit_price < entry_price
                
                all_results.append({
                    'symbol': sym,
                    'ts': int(ts_num[idx]),
                    'z': float(z[idx]),
                    'zt': zt,
                    'entry_price': float(entry_price),
                    'exit_price': float(exit_price),
                    'vc_green': int(vc_green),
                    'bb_pos': float(bb_pos[idx]) if not np.isnan(bb_pos[idx]) else 0,
                    'trend_dist': float(trend_dist[idx]) if not np.isnan(trend_dist[idx]) else 0,
                    'rsi': float(rsi[idx]) if not np.isnan(rsi[idx]) else 50,
                    'close': float(vc_close),
                    'move_fade': float(move_fade),
                    'win_fade': int(win_fade),
                    'move_ride': float(move_ride),
                    'win_ride': int(win_ride),
                })
    
    return all_results

def analyze(results, name=""):
    if not results:
        print(f"\n  [{name}] No results")
        return
    
    n = len(results)
    syms = set(r['symbol'] for r in results)
    
    fade_wins = sum(r['win_fade'] for r in results)
    ride_wins = sum(r['win_ride'] for r in results)
    
    print(f"\n  === {name}: {n} events across {len(syms)} symbols ===")
    print(f"  FADE: {fade_wins}/{n} = {fade_wins/n*100:.1f}%")
    print(f"  RIDE: {ride_wins}/{n} = {ride_wins/n*100:.1f}%")
    
    # By z threshold
    print(f"\n  --- By z_threshold ---")
    for zt in sorted(set(r['zt'] for r in results)):
        sub = [r for r in results if r['zt'] == zt]
        fw = sum(r['win_fade'] for r in sub)
        rw = sum(r['win_ride'] for r in sub)
        print(f"    z>{zt}: {len(sub):5d} | FADE={fw/len(sub)*100:5.1f}% RIDE={rw/len(sub)*100:5.1f}%")
    
    # By BB position
    print(f"\n  --- By BB position ---")
    for tag, lo, hi in [('Oversold', -2, -0.5), ('Mid', -0.5, 0.5), ('Overbought', 0.5, 2)]:
        sub = [r for r in results if lo <= r['bb_pos'] < hi]
        if len(sub) < 3: continue
        fw = sum(r['win_fade'] for r in sub)
        rw = sum(r['win_ride'] for r in sub)
        print(f"    BB {tag:>10}: {len(sub):5d} | FADE={fw/len(sub)*100:5.1f}% RIDE={rw/len(sub)*100:5.1f}%")
    
    # By trend distance
    print(f"\n  --- By trend distance (SMA50) ---")
    for tag, lo, hi in [('Strong downtrend', -50, -3), ('Slight downtrend', -3, -1), ('Flat', -1, 1), ('Slight uptrend', 1, 3), ('Strong uptrend', 3, 50)]:
        sub = [r for r in results if lo <= r['trend_dist'] < hi]
        if len(sub) < 3: continue
        fw = sum(r['win_fade'] for r in sub)
        rw = sum(r['win_ride'] for r in sub)
        print(f"    {tag:>20}: {len(sub):5d} | FADE={fw/len(sub)*100:5.1f}% RIDE={rw/len(sub)*100:5.1f}%")
    
    # By RSI
    print(f"\n  --- By RSI ---")
    for tag, lo, hi in [('Oversold (<30)', 0, 30), ('Low (30-45)', 30, 45), ('Mid (45-55)', 45, 55), ('High (55-70)', 55, 70), ('Overbought (>70)', 70, 100)]:
        sub = [r for r in results if lo <= r['rsi'] < hi]
        if len(sub) < 3: continue
        fw = sum(r['win_fade'] for r in sub)
        rw = sum(r['win_ride'] for r in sub)
        print(f"    {tag:>20}: {len(sub):5d} | FADE={fw/len(sub)*100:5.1f}% RIDE={rw/len(sub)*100:5.1f}%")
    
    # TRIZ MOD 1: Adaptive flip based on RSI
    print(f"\n  >> MOD 1: Adaptive flip (RIDE if RSI<30 or RSI>70, FADE otherwise):")
    adaptive = 0
    for r in results:
        if r['rsi'] < 30 or r['rsi'] > 70:
            adaptive += r['win_ride']
        else:
            adaptive += r['win_fade']
    print(f"    Adaptive WR: {adaptive/len(results)*100:.1f}% vs FADE={fade_wins/n*100:.1f}% vs RIDE={ride_wins/n*100:.1f}%")
    
    # TRIZ MOD 2: Adaptive flip based on BB position
    print(f"\n  >> MOD 2: Adaptive flip (RIDE if BB in extreme, FADE if BB in mid):")
    adaptive2 = 0
    for r in results:
        if abs(r['bb_pos']) > 0.7:
            # Price at extreme -> fade more aggressively
            adaptive2 += r['win_fade']
        elif abs(r['bb_pos']) < 0.3:
            # Price at center -> momentum tends to continue -> ride
            adaptive2 += r['win_ride']
        else:
            adaptive2 += r['win_fade']
    print(f"    Adaptive WR: {adaptive2/len(results)*100:.1f}%")
    
    # TRIZ MOD 3: Trend filter (skip fade if strong trend)
    print(f"\n  >> MOD 3: Trend filter (skip if |trend_dist|>3%):")
    filtered = [r for r in results if abs(r['trend_dist']) < 3]
    fw3 = sum(r['win_fade'] for r in filtered) / len(filtered) * 100 if filtered else 0
    rw3 = sum(r['win_ride'] for r in filtered) / len(filtered) * 100 if filtered else 0
    print(f"    Filtered: {len(filtered)} trades | FADE={fw3:.1f}% RIDE={rw3:.1f}%")
    
    # TRIZ MOD 4: Adaptive flip based on rolling WR of last 10 signals
    print(f"\n  >> MOD 4: Rolling WR adaptive flip (lookback=10):")
    results_sorted = sorted(results, key=lambda r: (r['symbol'], r['ts']))
    adaptive4_wins = 0
    adaptive4_total = 0
    from collections import deque
    for sym in sorted(set(r['symbol'] for r in results_sorted)):
        sym_results = [r for r in results_sorted if r['symbol'] == sym]
        recent = deque(maxlen=10)
        for i, r in enumerate(sym_results):
            if len(recent) < 10:
                recent.append(r['win_fade'])
                # Default to fade
                if i > 0:
                    if r['win_fade']:
                        adaptive4_wins += 1
                    adaptive4_total += 1
                continue
            
            rolling_fade_wr = sum(recent) / len(recent)
            recent.append(r['win_fade'])
            
            if rolling_fade_wr < 0.50:
                # Fade not working -> switch to ride
                if r['win_ride']:
                    adaptive4_wins += 1
            else:
                if r['win_fade']:
                    adaptive4_wins += 1
            adaptive4_total += 1
    
    print(f"    Adaptive WR: {adaptive4_wins/(adaptive4_total or 1)*100:.1f}% over {adaptive4_total} trades")
    
    # TRIZ MOD 5: Combined — RSI-based flip + trend filter
    print(f"\n  >> MOD 5: RSI flip + trend filter (the golden combination):")
    mod5 = 0
    mod5_total = 0
    for r in results:
        if abs(r['trend_dist']) > 3:
            continue  # skip strong trend
        mod5_total += 1
        if r['rsi'] < 30 or r['rsi'] > 70:
            mod5 += r['win_ride']  # extreme -> ride
        else:
            mod5 += r['win_fade']  # normal -> fade
    print(f"    WR: {mod5/(mod5_total or 1)*100:.1f}% over {mod5_total} trades")

def main():
    conn = get_conn()
    
    # Core symbols — top 6 by market cap + 4 mid-caps
    symbols = [
        'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
        'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT',
    ]
    
    print("=" * 80)
    print("VC TRIZ ANALYSIS — FAST MODE")
    print(f"Symbols: {symbols}")
    print("=" * 80)
    
    results = run_fast_vc(conn, symbols)
    analyze(results, "ALL SYMBOLS")
    
    # Per-symbol breakdown
    print("\n\n" + "=" * 80)
    print("PER-SYMBOL BREAKDOWN")
    print("=" * 80)
    from collections import defaultdict
    by_sym = defaultdict(list)
    for r in results:
        by_sym[r['symbol']].append(r)
    
    for sym in sorted(by_sym.keys()):
        sub = by_sym[sym]
        zt3 = [r for r in sub if r['zt'] == 3.0]
        if not zt3:
            continue
        fw = sum(r['win_fade'] for r in zt3) / len(zt3) * 100
        rw = sum(r['win_ride'] for r in zt3) / len(zt3) * 100
        # Mod 5 on this symbol
        m5 = [r for r in zt3 if abs(r['trend_dist']) < 3]
        if m5:
            m5_wins = 0
            for r in m5:
                if r['rsi'] < 30 or r['rsi'] > 70:
                    m5_wins += r['win_ride']
                else:
                    m5_wins += r['win_fade']
            m5_wr = m5_wins/len(m5)*100
        else:
            m5_wr = 0
        print(f"  {sym:>8}: {len(zt3):5d} trades | FADE={fw:5.1f}% RIDE={rw:5.1f}% | MOD5={m5_wr:5.1f}%")
    
    conn.close()

if __name__ == '__main__':
    main()
