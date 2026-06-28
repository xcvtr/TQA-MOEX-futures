"""
VC TRIZ Analysis: Ride/Fade flip, filters, MM, 5 modifications for 100%/year.
Tests the existing VC signal detector on major crypto pairs with proper analysis.
"""
import sys
sys.path.insert(0, '/home/user/projects/TQA-crypto')
import numpy as np
import datetime
from engine.db import get_conn

def compute_volume_zscore(volume, window=100):
    """Rolling z-score — only past data, no look-ahead."""
    z = np.full(len(volume), np.nan)
    for i in range(window, len(volume)):
        slice_ = volume[i-window:i]
        mean = np.mean(slice_)
        std = np.std(slice_, ddof=1)
        if std > 0:
            z[i] = (volume[i] - mean) / std
        else:
            z[i] = 0.0
    return z

def compute_atr(high, low, close, period=14):
    """Sequential ATR — no look-ahead."""
    atr = np.full(len(close), np.nan)
    tr = np.full(len(close), np.nan)
    for i in range(1, len(close)):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i-1])
        lc = abs(low[i] - close[i-1])
        tr[i] = max(hl, hc, lc)
    for i in range(period, len(close)):
        atr[i] = np.mean(tr[i-period+1:i+1])
    return atr, tr

def compute_adx(high, low, close, period=14):
    """Sequential ADX — no look-ahead."""
    n = len(close)
    plus_dm = np.full(n, np.nan)
    minus_dm = np.full(n, np.nan)
    tr = np.full(n, np.nan)
    
    for i in range(1, n):
        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i-1])
        lc = abs(low[i] - close[i-1])
        tr[i] = max(hl, hc, lc)
    
    atr_vals = np.full(n, np.nan)
    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    dx = np.full(n, np.nan)
    adx = np.full(n, np.nan)
    
    for i in range(period, n):
        atr_vals[i] = np.mean(tr[i-period+1:i+1])
        pdm_sum = np.mean(plus_dm[i-period+1:i+1])
        mdm_sum = np.mean(minus_dm[i-period+1:i+1])
        
        if atr_vals[i] > 0:
            plus_di[i] = 100 * pdm_sum / atr_vals[i]
            minus_di[i] = 100 * mdm_sum / atr_vals[i]
        
        di_sum = plus_di[i] + minus_di[i]
        di_diff = abs(plus_di[i] - minus_di[i])
        dx[i] = 100 * di_diff / di_sum if di_sum > 0 else 0
    
    for i in range(period * 2, n):
        adx[i] = np.mean(dx[i-period+1:i+1])
    
    return adx, plus_di, minus_di

def compute_bollinger_bands(close, period=20, n_std=2):
    """BB position: where is price relative to bands? -1 to +1."""
    bb_pos = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        window = close[i-period+1:i+1]
        mean = np.mean(window)
        std = np.std(window, ddof=1)
        upper = mean + n_std * std
        lower = mean - n_std * std
        if upper > lower:
            bb_pos[i] = (close[i] - lower) / (upper - lower) * 2 - 1  # -1 to +1
    return bb_pos

def compute_adx_percentile(adx, window=50):
    """Rolling ADX percentile — is current ADX high relative to recent history?"""
    pct = np.full(len(adx), np.nan)
    for i in range(window, len(adx)):
        hist = adx[i-window:i]
        if not np.isnan(adx[i]) and not np.isnan(hist).all():
            valid = hist[~np.isnan(hist)]
            pct[i] = sum(1 for v in valid if v <= adx[i]) / len(valid) * 100
    return pct

def run_vc_analysis(conn, symbols, z_threshold=3.0, lookahead=12):
    """Analyze VC events on 5m data for given symbols."""
    results = []
    
    for sym in symbols:
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines "
            "WHERE exchange='binance' AND symbol=%s AND interval='5m' "
            "ORDER BY timestamp",
            (sym,)
        ).fetchall()
        
        if not rows:
            continue
        
        ts = np.array([r['timestamp'] for r in rows])
        if isinstance(ts[0], datetime.datetime):
            ts_num = np.array([int(t.timestamp()) for t in ts])
        else:
            ts_num = ts // 1000 if ts[0] > 1e11 else ts
        
        close = np.array([r['close'] for r in rows], dtype=np.float64)
        high = np.array([r['high'] for r in rows], dtype=np.float64)
        low = np.array([r['low'] for r in rows], dtype=np.float64)
        volume = np.array([r['volume'] for r in rows], dtype=np.float64)
        
        n = len(close)
        print(f"  {sym}: {n} candles ({datetime.datetime.fromtimestamp(int(ts_num[0]))} - {datetime.datetime.fromtimestamp(int(ts_num[-1]))})")
        
        # Compute indicators
        z = compute_volume_zscore(volume, window=100)
        atr, tr = compute_atr(high, low, close, period=14)
        adx, pdi, mdi = compute_adx(high, low, close, period=14)
        bb_pos = compute_bollinger_bands(close, period=20)
        adx_pct = compute_adx_percentile(adx, window=50)
        
        # Filter analysis: try different z thresholds
        for zt in [2.0, 2.5, 3.0, 3.5, 4.0]:
            vc_indices = np.where(z > zt)[0]
            for idx in vc_indices:
                entry_idx = idx - 1
                if entry_idx < 0 or entry_idx >= n:
                    continue
                
                exit_idx = min(idx + lookahead, n - 1)
                if exit_idx >= n:
                    continue
                
                entry_price = close[entry_idx]
                exit_price = close[exit_idx]
                
                # Direction: price BEFORE vs AFTER VC candle
                # If price is rising into VC and falling after -> SHORT reversal signal
                # If price is falling into VC and rising after -> LONG reversal signal
                # We fade: SHORT on green VC, LONG on red VC
                
                vc_open = open_vals = np.array([r['open'] for r in rows], dtype=np.float64)[idx]
                vc_close = close[idx]
                
                # VC candle direction: GREEN (close > open) or RED (close < open)
                vc_green = vc_close > open_vals
                
                # Fade: if green VC, go SHORT. If red VC, go LONG.
                if vc_green:
                    # SHORT: profit if exit < entry
                    win_fade = exit_price < entry_price
                    move_fade = ((entry_price - exit_price) / entry_price) * 100  # positive = profit
                else:
                    # LONG: profit if exit > entry
                    win_fade = exit_price > entry_price
                    move_fade = ((exit_price - entry_price) / entry_price) * 100
                
                # Ride (follow VC direction): if green VC, go LONG. If red VC, go SHORT.
                if vc_green:
                    win_ride = exit_price > entry_price
                    move_ride = ((exit_price - entry_price) / entry_price) * 100
                else:
                    win_ride = exit_price < entry_price
                    move_ride = ((entry_price - exit_price) / entry_price) * 100
                
                # Best of both: pick fade or ride based on context
                win_best = win_fade or win_ride
                move_best = move_fade if abs(move_fade) >= abs(move_ride) else move_ride
                
                results.append({
                    'symbol': sym,
                    'ts': int(ts_num[idx]),
                    'z': float(z[idx]),
                    'z_threshold': zt,
                    'entry_price': float(entry_price),
                    'exit_price': float(exit_price),
                    'vc_close': float(vc_close),
                    'vc_green': int(vc_green),
                    'atr': float(atr[idx]) if not np.isnan(atr[idx]) else 0,
                    'adx': float(adx[idx]) if not np.isnan(adx[idx]) else 0,
                    'adx_pct': float(adx_pct[idx]) if not np.isnan(adx_pct[idx]) else 50,
                    'bb_pos': float(bb_pos[idx]) if not np.isnan(bb_pos[idx]) else 0,
                    'move_fade_pct': float(move_fade),
                    'win_fade': int(win_fade),
                    'move_ride_pct': float(move_ride),
                    'win_ride': int(win_ride),
                })
    
    return results

def print_analysis(results, label="VC"):
    """Print stats grouped by various dimensions."""
    if not results:
        print(f"  {label}: 0 events")
        return
    
    def wr(rows):
        return sum(r['win'] for r in rows) / len(rows) * 100 if rows else 0
    
    def avg_move(rows):
        return np.mean([r['move'] for r in rows]) if rows else 0
    
    # Overall
    fade_results = [r for r in results if 'win_fade' in r]
    ride_results = [r for r in results if 'win_ride' in r]
    
    print(f"\n  === {label}: {len(fade_results)} events across {len(set(r['symbol'] for r in fade_results))} symbols ===")
    
    # By z_threshold
    for zt in sorted(set(r['z_threshold'] for r in fade_results)):
        sub = [r for r in fade_results if r['z_threshold'] == zt]
        if len(sub) < 3:
            continue
        fade_wr = sum(r['win_fade'] for r in sub) / len(sub) * 100
        ride_wr = sum(r['win_ride'] for r in sub) / len(sub) * 100
        avg_z = np.mean([r['z'] for r in sub])
        print(f"    z>{zt:3.1f}: {len(sub):5d} events | Fade WR={fade_wr:5.1f}% | Ride WR={ride_wr:5.1f}% | avg z={avg_z:.1f}")
    
    # By ADX percentile
    print(f"\n  By ADX percentile:")
    for lo, hi, tag in [(0, 30, 'LOW'), (30, 70, 'MED'), (70, 100, 'HIGH')]:
        sub = [r for r in fade_results if lo <= r['adx_pct'] < hi]
        if len(sub) < 3:
            continue
        fade_wr = sum(r['win_fade'] for r in sub) / len(sub) * 100
        ride_wr = sum(r['win_ride'] for r in sub) / len(sub) * 100
        print(f"    ADX pct {lo:3d}-{hi:3d} ({tag}): {len(sub):5d} | Fade WR={fade_wr:5.1f}% | Ride WR={ride_wr:5.1f}%")
    
    # By BB position
    print(f"\n  By BB position:")
    for lo, hi, tag in [(-2, -0.5, 'OVERSOLD'), (-0.5, 0.5, 'MID'), (0.5, 2, 'OVERBOUGHT')]:
        sub = [r for r in fade_results if lo <= r['bb_pos'] < hi]
        if len(sub) < 3:
            continue
        fade_wr = sum(r['win_fade'] for r in sub) / len(sub) * 100
        ride_wr = sum(r['win_ride'] for r in sub) / len(sub) * 100
        print(f"    BB {tag:>10}: {len(sub):5d} | Fade WR={fade_wr:5.1f}% | Ride WR={ride_wr:5.1f}%")
    
    # Adaptive: pick fade vs ride based on ADX percentile
    print(f"\n  Adaptive flip (Fade when ADX<70pct, Ride when ADX>70pct):")
    adaptive_wins = 0
    for r in fade_results:
        if r['adx_pct'] > 70:
            adaptive_wins += r['win_ride']
        else:
            adaptive_wins += r['win_fade']
    print(f"    Adaptive WR: {adaptive_wins/len(fade_results)*100:.1f}% vs Fade-only WR: {sum(r['win_fade'] for r in fade_results)/len(fade_results)*100:.1f}%")

def main():
    conn = get_conn()
    
    # Major pairs (liquid, meaningful)
    major_symbols = [
        'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
        'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT',
        'MATICUSDT', 'UNIUSDT', 'ATOMUSDT', 'LTCUSDT', 'BCHUSDT',
        'TRXUSDT', 'NEARUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT',
    ]
    
    minor_symbols = [
        'PEPEUSDT', 'WIFUSDT', 'FETUSDT', 'INJUSDT', 'RUNEUSDT',
        'AAVEUSDT', 'CRVUSDT', 'MKRUSDT', 'COMPUSDT', 'SUSHIUSDT',
    ]
    
    print("=" * 80)
    print("VC TRIZ ANALYSIS: Major Pairs (20 symbols)")
    print("=" * 80)
    major_results = run_vc_analysis(conn, major_symbols)
    print_analysis(major_results, "MAJORS")
    
    print("\n" + "=" * 80)
    print("VC TRIZ ANALYSIS: Minor Pairs (10 symbols)")
    print("=" * 80)
    minor_results = run_vc_analysis(conn, minor_symbols)
    print_analysis(minor_results, "MINORS")
    
    # Combined analysis for TRIZ modifications
    all_results = major_results + minor_results
    print("\n\n" + "=" * 80)
    print("TRIZ MODIFICATION ANALYSIS")
    print("=" * 80)
    
    # Mod 1: Adaptive z_threshold based on rolling volatility
    print("\n--- Mod 1: Adaptive z_threshold ---")
    for zt in [2.0, 2.5, 3.0, 3.5, 4.0]:
        sub = [r for r in major_results if r['z_threshold'] == zt]
        if len(sub) < 10:
            continue
        fade_wr = sum(r['win_fade'] for r in sub) / len(sub) * 100
        ride_wr = sum(r['win_ride'] for r in sub) / len(sub) * 100
        print(f"  z>{zt}: {len(sub):4d} trades | Fade={fade_wr:5.1f}% | Ride={ride_wr:5.1f}%")
    
    # Mod 2: ADX filter (skip when trending)
    print("\n--- Mod 2: ADX filter ---")
    for adx_thresh in [20, 25, 30, 35, 40]:
        # Don't fade when ADX > threshold (trending)
        filtered = [r for r in major_results if r['adx'] <= adx_thresh]
        if len(filtered) < 10:
            continue
        fw = sum(r['win_fade'] for r in filtered) / len(filtered) * 100
        print(f"  Fade only if ADX < {adx_thresh:2d}: {len(filtered):4d} trades | WR={fw:5.1f}%")
    
    # Mod 3: BB position filter (fade only at extremes)
    print("\n--- Mod 3: BB position filter ---")
    for bb_thresh in [0.3, 0.5, 0.7, 0.9]:
        filtered = [r for r in major_results if abs(r['bb_pos']) > bb_thresh]
        if len(filtered) < 10:
            continue
        fw = sum(r['win_fade'] for r in filtered) / len(filtered) * 100
        print(f"  Fade only |BB| > {bb_thresh:.1f}: {len(filtered):4d} trades | WR={fw:5.1f}%")
    
    # Mod 4: Rolling WR adaptive flip (TRIZ feedback principle)
    print("\n--- Mod 4: Adaptive flip (rolling WR) ---")
    # Group by symbol + z_threshold, simulate rolling WR
    from collections import defaultdict
    by_sym = defaultdict(list)
    for r in all_results:
        by_sym[r['symbol']].append(r)
    
    for sym in sorted(by_sym.keys()):
        rows = sorted(by_sym[sym], key=lambda x: x['ts'])
        for zt in [2.5, 3.0]:
            sub = [r for r in rows if r['z_threshold'] == zt]
            if len(sub) < 10:
                continue
            
            # Simulate rolling WR of last N signals
            for n_lookback in [5, 10, 15, 20]:
                adaptive_wins = 0
                for i in range(n_lookback, len(sub)):
                    recent = sub[i-n_lookback:i]
                    recent_fade_wr = sum(r['win_fade'] for r in recent) / len(recent)
                    # If fade WR < 50%, flip to ride for this signal
                    if recent_fade_wr < 0.50:
                        adaptive_wins += sub[i]['win_ride']
                    else:
                        adaptive_wins += sub[i]['win_fade']
                
                baseline_wins = sum(r['win_fade'] for r in sub)
                total = len(sub)
                if total >= n_lookback + 1:
                    avail = total - n_lookback
                    print(f"  {sym:>10} z>{zt} lookback={n_lookback:2d}: {avail:4d} trades | Adaptive={adaptive_wins/avail*100:5.1f}% vs Fade={baseline_wins/total*100:5.1f}%")
    
    # Mod 5: Combined filter (ADX<25 AND |BB|>0.5)
    print("\n--- Mod 5: Combined filters ---")
    for zt in [2.5, 3.0]:
        combined = [r for r in all_results if r['z_threshold'] == zt]
        good = [r for r in combined if r['adx'] <= 25 and abs(r['bb_pos']) > 0.5]
        if len(good) < 5:
            print(f"  z>{zt} AND ADX<25 AND |BB|>0.5: only {len(good)} trades")
            continue
        fw = sum(r['win_fade'] for r in good) / len(good) * 100
        base = sum(r['win_fade'] for r in combined) / len(combined) * 100
        print(f"  z>{zt} + ADX<25 + |BB|>0.5: {len(good):4d}/{len(combined):4d} trades | WR={fw:5.1f}% vs all={base:5.1f}%")
    
    conn.close()
    print("\nDONE")

if __name__ == '__main__':
    main()
