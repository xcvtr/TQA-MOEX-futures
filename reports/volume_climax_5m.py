"""Volume Climax Analysis on 5m data from crypto DB."""
import sys
sys.path.insert(0, '/home/user/projects/TQA-crypto')
import numpy as np
from engine.db import get_conn

def load_data(conn, symbol):
    """Load all 5m candles for a symbol sorted by timestamp."""
    rows = conn.execute(
        "SELECT timestamp, close, volume FROM klines "
        "WHERE exchange='binance' AND symbol=%s AND interval='5m' "
        "ORDER BY timestamp",
        (symbol,)
    ).fetchall()
    if not rows:
        return None, None, None
    ts = np.array([r['timestamp'] for r in rows], dtype=np.int64)
    close = np.array([r['close'] for r in rows], dtype=np.float64)
    volume = np.array([r['volume'] for r in rows], dtype=np.float64)
    return ts, close, volume

def compute_volume_zscore(volume, window=100):
    """Compute rolling z-score: z[i] = (vol[i] - mean(vol[i-window:i])) / std(vol[i-window:i]).
    Uses only data BEFORE current candle (no look-ahead)."""
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

def analyze_volume_climax(ts, close, volume, z_threshold=3.0, lookahead=12):
    """Find Volume Climax events and check reversals."""
    z = compute_volume_zscore(volume, window=100)
    
    vc_indices = np.where(z > z_threshold)[0]
    
    results = []
    for idx in vc_indices:
        # Entry price: close of candle BEFORE the VC candle (no look-ahead)
        entry_idx = idx - 1
        if entry_idx < 0:
            continue
        entry_price = close[entry_idx]
        
        # Exit price: close of candle `lookahead` periods after VC
        exit_idx = idx + lookahead
        if exit_idx >= len(close):
            continue
        
        exit_price = close[exit_idx]
        
        # VC candle info
        vc_close = close[idx]
        vc_volume = volume[idx]
        vc_z = z[idx]
        vc_time = ts[idx]
        
        # SHORT reversal: exit price < entry price (price went down after VC)
        short_reversal = exit_price < entry_price
        # LONG reversal: exit price > entry price (price went up after VC)
        long_reversal = exit_price > entry_price
        
        # Movement in %
        move_pct = ((exit_price - entry_price) / entry_price) * 100
        
        # Direction of reversal (1 for profitable short, -1 for profitable long, 0 for no clear)
        if short_reversal:
            rev_type = 'SHORT'
            win = 1
        elif long_reversal:
            rev_type = 'LONG'
            win = 1
        else:
            rev_type = 'NONE'
            win = 0
        
        results.append({
            'vc_time': vc_time,
            'vc_close': vc_close,
            'vc_volume': vc_volume,
            'vc_z': vc_z,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'move_pct': move_pct,
            'rev_type': rev_type,
            'win': win,
        })
    
    return results

def compute_basic_stats(results, close):
    """Compute summary stats per symbol."""
    total = len(results)
    if total == 0:
        return {'total': 0, 'wins': 0, 'win_rate': 0.0, 'avg_move': 0.0, 'avg_win_move': 0.0, 'avg_loss_move': 0.0}
    
    wins = sum(1 for r in results if r['win'])
    moves = [r['move_pct'] for r in results]
    win_moves = [r['move_pct'] for r in results if r['win']]
    loss_moves = [r['move_pct'] for r in results if not r['win']]
    
    return {
        'total': total,
        'wins': wins,
        'win_rate': wins / total * 100,
        'avg_move': np.mean(moves),
        'avg_win_move': np.mean(win_moves) if win_moves else 0.0,
        'avg_loss_move': np.mean(loss_moves) if loss_moves else 0.0,
    }

def main():
    conn = get_conn()
    
    # Step 2: Get all symbols with 5m data
    symbols = [r['symbol'] for r in conn.execute(
        "SELECT DISTINCT symbol FROM klines WHERE interval='5m' AND exchange='binance' ORDER BY symbol"
    ).fetchall()]
    
    print(f"Found {len(symbols)} symbols with 5m data: {symbols}\n")
    print("=" * 120)
    print(f"{'Symbol':<20} {'Total 5m':<10} {'VC Events':<10} {'Wins':<8} {'WR(%)':<8} {'Avg Move(%)':<12} {'Avg Win(%)':<12} {'Avg Loss(%)':<12} {'Data Range'}")
    print("=" * 120)
    
    all_stats = []
    detailed_rows = []
    
    for symbol in symbols:
        ts, close, volume = load_data(conn, symbol)
        if ts is None:
            print(f"{symbol:<20} {'NO DATA':<10}")
            continue
        
        n_candles = len(close)
        start_ts = ts[0]
        end_ts = ts[-1]
        
        import datetime
        start_str = datetime.datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d')
        end_str = datetime.datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d')
        
        results = analyze_volume_climax(ts, close, volume)
        stats = compute_basic_stats(results, close)
        
        all_stats.append((symbol, stats, results, start_str, end_str, n_candles))
        
        print(f"{symbol:<20} {n_candles:<10} {stats['total']:<10} {stats['wins']:<8} {stats['win_rate']:<8.1f} {stats['avg_move']:<12.4f} {stats['avg_win_move']:<12.4f} {stats['avg_loss_move']:<12.4f} {start_str} - {end_str}")
        
        # Store detailed results
        for r in results:
            detailed_rows.append({
                'symbol': symbol,
                'vc_time': r['vc_time'],
                'vc_z': r['vc_z'],
                'entry_price': r['entry_price'],
                'exit_price': r['exit_price'],
                'move_pct': r['move_pct'],
                'rev_type': r['rev_type'],
            })
    
    print("=" * 120)
    
    # Sort by win rate descending
    all_stats_sorted = sorted(all_stats, key=lambda x: x[1]['win_rate'], reverse=True)
    
    print("\n\n=== SORTED BY WIN RATE (descending) ===")
    print("=" * 80)
    print(f"{'Symbol':<20} {'VC Events':<12} {'Wins':<8} {'WR(%)':<10} {'Avg Move(%)':<12}")
    print("=" * 80)
    for symbol, stats, _, _, _, n in all_stats_sorted:
        print(f"{symbol:<20} {stats['total']:<12} {stats['wins']:<8} {stats['win_rate']:<10.1f} {stats['avg_move']:<12.4f}")
    print("=" * 80)
    
    # Overall stats
    total_vc = sum(s['total'] for _, s, _, _, _, _ in all_stats_sorted)
    total_wins = sum(s['wins'] for _, s, _, _, _, _ in all_stats_sorted)
    all_moves = []
    for _, _, results, _, _, _ in all_stats_sorted:
        all_moves.extend([r['move_pct'] for r in results])
    
    if total_vc > 0:
        print(f"\nOverall: {total_vc} VC events, {total_wins} wins ({total_wins/total_vc*100:.1f}%), avg move: {np.mean(all_moves):.4f}%")
    
    # Save detailed results to a file
    if detailed_rows:
        import csv, os
        out_path = '/home/user/volume_climax_results_5m.csv'
        with open(out_path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['symbol', 'vc_time', 'vc_z', 'entry_price', 'exit_price', 'move_pct', 'rev_type'])
            w.writeheader()
            w.writerows(detailed_rows)
        print(f"\nDetailed results saved to {out_path}")
    
    conn.close()

if __name__ == '__main__':
    main()
