"""
Deep testing for all 7 MOEX strategies.
Implements tasks from docs/plans/2026-06-08-deep-testing-plan.md
"""
import sys, os, json, random, math
from datetime import datetime, timedelta, timezone
from collections import OrderedDict

import psycopg2

sys.path.insert(0, '/home/user/projects/TQA-MOEX')
from trading_bot.engine import detect_signals as vs_detect
from trading_bot.reversion_engine import detect_mean_reversion_signals as rev_detect
from trading_bot.ob_engine import detect_order_block_signals as ob_detect
from trading_bot.vwap_engine import detect_vwap_signals as vwap_detect
from trading_bot.new_strategies import detect_otc_signals, detect_retail_trap_signals, detect_oi_divergence_signals

from trading_bot import (
    DEFAULT_CONFIG, DEFAULT_REVERSION_CONFIG, DEFAULT_OB_CONFIG,
)
from trading_bot.vwap_engine import DEFAULT_VWAP_CONFIG

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

# ─── Data Loaders ────────────────────────────────────────────────────────────

def load_ohlcv(symbol, days=365):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol=%s AND time>=%s ORDER BY time", (symbol, since))
    rows = []
    for r in cur:
        rows.append({'time': r[0], 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])})
    cur.close(); conn.close()
    return rows

def load_oi(symbol, days=365):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi FROM moex_prices_5m_oi WHERE symbol=%s AND time>=%s ORDER BY time", (symbol, since))
    rows = []
    for r in cur:
        rows.append({'time': r[0], 'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]), 'yur_buy': float(r[3]), 'yur_sell': float(r[4]), 'total_oi': float(r[5])})
    cur.close(); conn.close()
    return rows

def merge_ohlcv_oi(ohlcv, oi):
    oi_by_time = {str(r['time'])[:16]: r for r in oi}
    merged = []
    for r in ohlcv:
        oi_row = oi_by_time.get(str(r['time'])[:16])
        if oi_row:
            merged.append({**r, **oi_row})
    return merged

# ─── Data Format Converters ──────────────────────────────────────────────────

def to_ohlcv_tuples(dict_rows):
    """Convert list of dicts to list of 6-tuples (time, open, high, low, close, volume)"""
    return [(r['time'], r['open'], r['high'], r['low'], r['close'], r['volume']) for r in dict_rows]

def to_vs_tuples(dict_rows):
    """Convert dict rows with OI to 8-tuples for VS: (time, fiz_buy, fiz_sell, yur_buy, yur_sell, close, volume, open)"""
    return [(str(r['time']), float(r.get('fiz_buy',0)), float(r.get('fiz_sell',0)),
             float(r.get('yur_buy',0)), float(r.get('yur_sell',0)),
             float(r['close']), float(r['volume']), float(r['open'])) for r in dict_rows]

# ─── Strategy Registry ──────────────────────────────────────────────────────

STRATEGIES = OrderedDict({
    'vs': {
        'name': 'Volume Surge',
        'fn': vs_detect,
        'tickers': ['HS','KC','DX','HY','BM'],
        'config': DEFAULT_CONFIG,
        'prepare': lambda rows, is_vs=True: to_vs_tuples(rows) if is_vs else rows,
        'needs_oi': True,
    },
    'reversion': {
        'name': 'Mean Reversion',
        'fn': lambda *a, **kw: rev_detect(*a, **kw),
        'tickers': ['NM','AF'],
        'config': DEFAULT_REVERSION_CONFIG,
        'prepare': lambda rows, _sym: to_ohlcv_tuples(rows),
        'needs_oi': False,
    },
    'ob': {
        'name': 'Order Block',
        'fn': lambda *a, **kw: ob_detect(*a, **kw),
        'tickers': ['SBERF','BR'],
        'config': DEFAULT_OB_CONFIG,
        'prepare': lambda rows, _sym: to_ohlcv_tuples(rows),
        'needs_oi': False,
    },
    'vwap': {
        'name': 'VWAP',
        'fn': lambda *a, **kw: vwap_detect(*a, **kw),
        'tickers': ['GZ','Eu','SR','Si','MC'],
        'config': DEFAULT_VWAP_CONFIG,
        'prepare': lambda rows, _sym: rows,  # vwap expects dicts
        'needs_oi': False,
    },
    'otc': {
        'name': 'OTC',
        'fn': lambda *a, **kw: detect_otc_signals(*a, **kw),
        'tickers': ['CNYRUBF','Si','CC','SV'],
        'config': {'oi_z_thresh': 0.3, 'price_z_thresh': 0.5, 'horizon': 12},
        'prepare': lambda rows, _sym: rows,  # merged dicts
        'needs_oi': True,
    },
    'retail_trap': {
        'name': 'Retail Trap',
        'fn': lambda *a, **kw: detect_retail_trap_signals(*a, **kw),
        'tickers': ['CNYRUBF','GZ','BR'],
        'config': {'fiz_z_thresh': 1.5, 'horizon': 12},
        'prepare': lambda rows, _sym: rows,
        'needs_oi': True,
    },
    'oi_divergence': {
        'name': 'OI Divergence',
        'fn': lambda *a, **kw: detect_oi_divergence_signals(*a, **kw),
        'tickers': ['SV','BR','BM'],
        'config': {'lookback': 20, 'horizon': 3, 'extreme_window': 10, 'bear_threshold': 0.9, 'bull_threshold': 1.1},
        'prepare': lambda rows, _sym: rows,
        'needs_oi': True,
    },
})

# ─── Stats ───────────────────────────────────────────────────────────────────

def compute_stats(signals):
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0, 'avg_ret': 0.0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins) / n * 100 if n else 0
    sum_wins = sum(wins) if wins else 0
    sum_losses = abs(sum(losses)) if losses else 0
    pf = sum_wins / sum_losses if sum_losses > 0 else (999.99 if sum_wins > 0 else 0)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd:
            max_dd = dd
    avg_ret = sum(returns) / n if n else 0
    return {'n': n, 'wr': round(wr, 1), 'pf': round(pf, 2), 'dd': round(max_dd, 1), 'avg_ret': round(avg_ret, 4)}

def filter_signals_by_index(signals, min_idx, max_idx):
    """Return signals where idx is in [min_idx, max_idx)"""
    return [s for s in signals if min_idx <= s['idx'] < max_idx]

# ─── Load data for strategy ──────────────────────────────────────────────────

def load_and_prepare(strategy_name, ticker, days=456):
    """Load data for a strategy+ticker and return prepared data list."""
    info = STRATEGIES[strategy_name]
    ohlcv = load_ohlcv(ticker, days)
    if not ohlcv:
        return []
    
    if info['needs_oi']:
        oi = load_oi(ticker, days)
        merged = merge_ohlcv_oi(ohlcv, oi)
        if not merged:
            return []
        # For VS, convert to tuples; for others, keep as dicts
        if strategy_name == 'vs':
            return to_vs_tuples(merged)
        return merged
    else:
        # Prepare for the strategy
        return info['prepare'](ohlcv, ticker)

def run_strategy(strategy_name, ticker, prepared_data, config=None):
    """Run a strategy on prepared data and return signals."""
    info = STRATEGIES[strategy_name]
    fn = info['fn']
    cfg = config if config is not None else info['config']
    
    if strategy_name == 'vs':
        return fn(prepared_data, cfg)
    elif strategy_name in ('reversion', 'ob', 'vwap'):
        return fn(ticker, prepared_data, cfg)
    else:  # otc, retail_trap, oi_divergence
        return fn(prepared_data, cfg)

# ─── Load full ticker lookup ────────────────────────────────────────────────

def load_all_tickers():
    """Load all ticker symbols from DB."""
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM moex_prices_5m ORDER BY symbol")
    tickers = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return tickers

# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1: Walk-Forward Validation
# ═══════════════════════════════════════════════════════════════════════════════

def task1_walkforward(save=True):
    """4-fold walk-forward validation for all strategies."""
    print("\n" + "="*70)
    print("  TASK 1: WALK-FORWARD VALIDATION")
    print("="*70)
    
    results = []
    lines = ["=== WALK-FORWARD VALIDATION ===\n"]
    
    for skey, sinfo in STRATEGIES.items():
        for ticker in sinfo['tickers']:
            print(f"\n  --- {sinfo['name']} on {ticker} ---")
            
            data = load_and_prepare(skey, ticker, days=730)  # 2 years
            if not data:
                print(f"  ⚠ No data for {ticker}, skipping")
                continue
            if len(data) < 500:
                print(f"  ⚠ Too little data ({len(data)} bars), skipping")
                continue
            
            n = len(data)
            chunk = n // 7  # 7 chunks for 4 non-overlapping 75/25 splits
            if chunk < 50:
                print(f"  ⚠ Chunk too small ({chunk}), skipping")
                continue
            
            fold_results = []
            
            for fold in range(4):
                train_start = fold * chunk
                train_end = train_start + 3 * chunk
                test_start = train_end
                test_end = min(test_start + chunk, n)
                
                if test_end <= test_start:
                    continue
                
                train_data = data[train_start:train_end]
                test_data = data[test_start:test_end]
                
                if len(train_data) < 100 or len(test_data) < 20:
                    continue
                
                combined = train_data + test_data
                train_len = len(train_data)
                
                signals = run_strategy(skey, ticker, combined)
                
                # Filter signals in test period
                test_signals = []
                for s in signals:
                    idx = s.get('idx', -1)
                    if idx >= train_len and idx < train_len + len(test_data):
                        # Recalculate idx relative to original data
                        s_copy = dict(s)
                        s_copy['idx'] = idx  # keep original idx
                        test_signals.append(s_copy)
                
                stats = compute_stats(test_signals)
                fold_results.append({
                    'fold': fold + 1,
                    'n': stats['n'],
                    'wr': stats['wr'],
                    'pf': stats['pf'],
                    'dd': stats['dd'],
                    'signals': test_signals,
                })
                
                print(f"    Fold {fold+1}: train={train_start}-{train_end} test={test_start}-{test_end} "
                      f"n={stats['n']} WR={stats['wr']}% PF={stats['pf']} DD={stats['dd']}%")
            
            if len(fold_results) >= 2:
                avg_wr = sum(f['wr'] for f in fold_results) / len(fold_results)
                std_wr = (sum((f['wr']-avg_wr)**2 for f in fold_results) / len(fold_results))**0.5
                avg_pf = sum(f['pf'] for f in fold_results) / len(fold_results)
                std_pf = (sum((f['pf']-avg_pf)**2 for f in fold_results) / len(fold_results))**0.5
                
                line = f"\n=== Walk-Forward: {sinfo['name']} on {ticker} ==="
                print(line)
                lines.append(line)
                for f in fold_results:
                    l = f"  Fold {f['fold']}: WR={f['wr']}% PF={f['pf']} n={f['n']}"
                    print(l)
                    lines.append(l)
                summary = f"  Average: WR={avg_wr:.1f}%±{std_wr:.1f}% PF={avg_pf:.2f}±{std_pf:.2f}"
                print(summary)
                lines.append(summary)
                
                results.append({
                    'strategy': sinfo['name'],
                    'ticker': ticker,
                    'folds': fold_results,
                    'avg_wr': round(avg_wr, 1),
                    'std_wr': round(std_wr, 1),
                    'avg_pf': round(avg_pf, 2),
                    'std_pf': round(std_pf, 2),
                })
            else:
                l = f"\n  {sinfo['name']} on {ticker}: insufficient folds ({len(fold_results)})"
                print(l)
                lines.append(l)
    
    lines.append("\n")
    output = '\n'.join(lines)
    if save:
        os.makedirs('/home/user/projects/TQA-MOEX/docs/backtest', exist_ok=True)
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_1_walkforward.txt', 'w') as f:
            f.write(output)
    return results

# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2: SHORT Return Cross-Check
# ═══════════════════════════════════════════════════════════════════════════════

def task2_return_crosscheck(save=True):
    """Verify SHORT returns are computed as (entry - exit) / entry."""
    print("\n" + "="*70)
    print("  TASK 2: RETURN CROSS-CHECK")
    print("="*70)
    
    lines = ["=== RETURN CROSS-CHECK ===\n"]
    total_errors = 0
    total_signals = 0
    
    for skey, sinfo in STRATEGIES.items():
        # Pick top ticker (first in list)
        ticker = sinfo['tickers'][0]
        print(f"\n  --- {sinfo['name']} on {ticker} ---")
        
        data = load_and_prepare(skey, ticker, days=365)
        if not data:
            l = f"  {sinfo['name']} on {ticker}: NO DATA"
            print(f"  {l}")
            lines.append(l)
            continue
        
        signals = run_strategy(skey, ticker, data)
        
        errors = 0
        n_check = min(100, len(signals))
        lines.append(f"\n{sinfo['name']} on {ticker} ({n_check} signals):")
        
        for s in signals[:n_check]:
            entry = s['entry']
            exit_price = s['exit']
            direction = s['direction']
            
            if direction == 'LONG':
                expected_ret = (exit_price - entry) / entry * 100
            else:
                expected_ret = (entry - exit_price) / entry * 100
            
            got = s['return_pct']
            if abs(round(expected_ret, 4) - got) > 0.001:
                errors += 1
                msg = f"  ERROR {direction} entry={entry} exit={exit_price} got={got} expected={expected_ret:.4f}"
                print(f"  {msg}")
                lines.append(msg)
        
        status = f"  {sinfo['name']} on {ticker}: {errors} errors / {n_check} signals"
        print(f"  {status}")
        lines.append(status)
        total_errors += errors
        total_signals += n_check
    
    summary = f"\nTotal: {total_errors} errors / {total_signals} signals"
    print(summary)
    lines.append(summary)
    lines.append("")
    
    output = '\n'.join(lines)
    if save:
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_2_return_crosscheck.txt', 'w') as f:
            f.write(output)
    return total_errors

# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3: Parameter Sensitivity
# ═══════════════════════════════════════════════════════════════════════════════

def task3_sensitivity(save=True):
    """Test parameter ±10%, ±20% for VWAP and OB."""
    print("\n" + "="*70)
    print("  TASK 3: PARAMETER SENSITIVITY")
    print("="*70)
    
    # VWAP on GZ
    vwap_variations = {
        'dev_thresh': [1.6, 1.8, 2.0, 2.2, 2.4],
        'horizon': [6, 9, 12, 15, 18],
        'vwap_window': [10, 15, 20, 30, 50],
    }
    base_vwap = {'dev_thresh': 2.0, 'horizon': 12, 'vwap_window': 20, 'atr_period': 14}
    
    # OB on SBERF
    ob_variations = {
        'body_mul': [1.2, 1.35, 1.5, 1.65, 1.8],
        'horizon': [2, 3, 4, 6, 8],
    }
    base_ob = {'body_mul': 1.5, 'range_mul': 1.2, 'horizon': 4, 'lookback': 20, 'min_history': 50, 'max_lookback_bars': 5}
    
    lines = []
    
    for label, strategy_key, ticker, base_config, variations in [
        ("VWAP on GZ", 'vwap', 'GZ', base_vwap, vwap_variations),
        ("OB on SBERF", 'ob', 'SBERF', base_ob, ob_variations),
    ]:
        print(f"\n  --- {label} ---")
        lines.append(f"\n=== Parameter Sensitivity: {label} ===\n")
        
        data = load_and_prepare(strategy_key, ticker, days=365)
        if not data:
            lines.append(f"  No data for {ticker}\n")
            continue
        
        # Baseline
        base_signals = run_strategy(strategy_key, ticker, data, base_config)
        base_stats = compute_stats(base_signals)
        base_wr = base_stats['wr']
        lines.append(f"  Base params: WR={base_wr}% PF={base_stats['pf']} n={base_stats['n']}")
        print(f"    Base: WR={base_wr}% PF={base_stats['pf']} n={base_stats['n']}")
        
        threshold = base_wr - 5.0
        robust_count = 0
        total_tests = 0
        
        for param_name, values in variations.items():
            lines.append(f"\n  Parameter: {param_name}")
            print(f"    Testing {param_name}: {values}")
            
            for val in values:
                test_config = dict(base_config)
                test_config[param_name] = val
                signals = run_strategy(strategy_key, ticker, data, test_config)
                stats = compute_stats(signals)
                
                if param_name in base_config and base_config[param_name] == val:
                    tag = " (BASE)"
                else:
                    tag = ""
                
                change = stats['wr'] - base_wr
                fragile = " ⚠ FRAGILE" if change < -5 else ""
                if change >= -5:
                    robust_count += 1
                total_tests += 1
                
                l = f"    {param_name}={val}: WR={stats['wr']}% PF={stats['pf']} n={stats['n']} (Δ={change:+.1f}%){tag}{fragile}"
                lines.append(l)
                print(l)
        
        robust_pct = robust_count / total_tests * 100 if total_tests else 0
        verdict = "ROBUST" if robust_pct >= 80 else "MODERATE" if robust_pct >= 50 else "FRAGILE"
        lines.append(f"\n  Verdict: {verdict} ({robust_count}/{total_tests} configs within 5% of base WR)")
        print(f"    Verdict: {verdict} ({robust_count}/{total_tests} configs within 5% of base WR)")
        lines.append("")
    
    output = '\n'.join(lines)
    if save:
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_3_sensitivity.txt', 'w') as f:
            f.write(output)
    return lines

# ═══════════════════════════════════════════════════════════════════════════════
# TASK 4: Signal Overlap Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def task4_signal_overlap(save=True):
    """Check if different strategies signal on the same bars."""
    print("\n" + "="*70)
    print("  TASK 4: SIGNAL OVERLAP ANALYSIS")
    print("="*70)
    
    lines = ["=== SIGNAL OVERLAP ANALYSIS ===\n"]
    
    # Pairs that share tickers
    pairs_to_check = [
        ('reversion', 'vwap'),  # shared tickers? no: NM/AF vs GZ/Eu/SR/Si/MC
        ('ob', 'vwap'),         # SBERF/BR vs GZ/Eu/SR/Si/MC
        ('ob', 'retail_trap'),  # BR is shared
        ('reversion', 'ob'),    # no shared
        ('otc', 'retail_trap'), # CNYRUBF is shared
        ('oi_divergence', 'ob'), # BR is shared
        ('oi_divergence', 'vs'), # BM vs HS/KC/DX/HY/BM — BM is shared!
    ]
    
    for sa, sb in pairs_to_check:
        info_a = STRATEGIES[sa]
        info_b = STRATEGIES[sb]
        shared = [t for t in info_a['tickers'] if t in info_b['tickers']]
        
        if not shared:
            l = f"\n{info_a['name']} vs {info_b['name']}: no shared tickers"
            print(l)
            lines.append(l)
            continue
        
        for ticker in shared:
            l = f"\n{info_a['name']} vs {info_b['name']} on {ticker}:"
            print(l)
            lines.append(l)
            
            data_a = load_and_prepare(sa, ticker, days=365)
            data_b = load_and_prepare(sb, ticker, days=365)
            
            if not data_a or not data_b:
                lines.append(f"  No data for {ticker}")
                continue
            
            sig_a = run_strategy(sa, ticker, data_a)
            sig_b = run_strategy(sb, ticker, data_b)
            
            # Build time lookup for set A
            times_a = {}
            for s in sig_a:
                t = str(s.get('time', ''))[:16]  # minute precision
                times_a[t] = times_a.get(t, 0) + 1
            
            overlapping = 0
            for s in sig_b:
                t = str(s.get('time', ''))[:16]
                if t in times_a:
                    overlapping += 1
            
            overlap_ratio = overlapping / min(len(sig_a), len(sig_b)) * 100 if min(len(sig_a), len(sig_b)) > 0 else 0
            l = f"  Signals A={len(sig_a)} B={len(sig_b)} overlapping={overlapping} ratio={overlap_ratio:.1f}%"
            print(f"  {l}")
            lines.append(f"  {l}")
    
    lines.append("\n")
    output = '\n'.join(lines)
    if save:
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_4_overlap.txt', 'w') as f:
            f.write(output)
    return lines

# ═══════════════════════════════════════════════════════════════════════════════
# TASK 5: Market Regime Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_adx(highs, lows, closes, period=14):
    """Calculate ADX values using correct Wilder's smoothing."""
    n = len(highs)
    if n < period + 1:
        return [0] * n
    
    true_range = [0.0] * n
    for i in range(1, n):
        true_range[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    
    up_move = [0.0] * n
    down_move = [0.0] * n
    for i in range(1, n):
        up_move[i] = highs[i] - highs[i-1]
        down_move[i] = lows[i-1] - lows[i]
    
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        if up_move[i] > down_move[i] and up_move[i] > 0:
            plus_dm[i] = up_move[i]
        if down_move[i] > up_move[i] and down_move[i] > 0:
            minus_dm[i] = down_move[i]
    
    atr = [0.0] * n
    smoothed_plus = [0.0] * n
    smoothed_minus = [0.0] * n
    plus_di = [0.0] * n
    minus_di = [0.0] * n
    dx = [0.0] * n
    adx = [0.0] * n
    
    # First smoothed values at index = period
    atr[period] = sum(true_range[1:period+1]) / period
    smoothed_plus[period] = sum(plus_dm[1:period+1]) / period
    smoothed_minus[period] = sum(minus_dm[1:period+1]) / period
    
    if atr[period] > 0:
        plus_di[period] = smoothed_plus[period] / atr[period] * 100
        minus_di[period] = smoothed_minus[period] / atr[period] * 100
    
    for i in range(period+1, n):
        atr[i] = (atr[i-1] * (period-1) + true_range[i]) / period
        smoothed_plus[i] = (smoothed_plus[i-1] * (period-1) + plus_dm[i]) / period
        smoothed_minus[i] = (smoothed_minus[i-1] * (period-1) + minus_dm[i]) / period
        
        if atr[i] > 0:
            plus_di[i] = smoothed_plus[i] / atr[i] * 100
            minus_di[i] = smoothed_minus[i] / atr[i] * 100
        
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = abs(plus_di[i] - minus_di[i]) / di_sum * 100
    
    # First ADX value at index 2*period
    adx_start = 2 * period
    if n > adx_start:
        adx[adx_start] = sum(dx[period+1:adx_start+1]) / period
    
    for i in range(adx_start+1, n):
        adx[i] = (adx[i-1] * (period-1) + dx[i]) / period
    
    return adx

def calculate_atr(highs, lows, closes, period=14):
    """Calculate ATR values."""
    n = len(highs)
    if n < 2:
        return [0] * n
    tr = [0] * n
    for i in range(1, n):
        tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    atr = [0] * n
    atr[period] = sum(tr[1:period+1]) / period
    for i in range(period+1, n):
        atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    atr[0] = tr[0] if tr[0] else 0
    for i in range(1, period):
        atr[i] = sum(tr[1:i+1]) / i if i > 0 else tr[i]
    return atr


def task5_regime_analysis(save=True):
    """Analyze strategy performance in different market regimes."""
    print("\n" + "="*70)
    print("  TASK 5: MARKET REGIME ANALYSIS")
    print("="*70)
    
    lines = ["=== MARKET REGIME ANALYSIS ===\n"]
    
    analyses = [
        ('vwap', 'GZ', 'VWAP on GZ', {'dev_thresh': 2.0, 'horizon': 12, 'vwap_window': 20, 'atr_period': 14}),
        ('ob', 'SBERF', 'OB on SBERF', None),
    ]
    
    for skey, ticker, label, config in analyses:
        print(f"\n  --- {label} ---")
        lines.append(f"\n=== {label} ===\n")
        
        data = load_and_prepare(skey, ticker, days=365)
        if not data:
            lines.append("  No data\n")
            continue
        
        # Get dict format for ATR/ADX calculation
        if skey == 'vwap':
            dict_data = data
        else:
            # Convert tuples to dicts
            dict_data = [{'time': r[0], 'open': r[1], 'high': r[2], 'low': r[3], 'close': r[4], 'volume': r[5]} for r in data]
        
        closes = [r['close'] for r in dict_data]
        highs = [r['high'] for r in dict_data]
        lows = [r['low'] for r in dict_data]
        
        adx = calculate_adx(highs, lows, closes, 14)
        atr = calculate_atr(highs, lows, closes, 14)
        
        # Run strategy on full data
        c = config if config else STRATEGIES[skey]['config']
        signals = run_strategy(skey, ticker, data, c)
        
        # Classify each signal's regime
        regimes = {'TRENDING': [], 'SIDEWAYS': [], 'MIXED': []}
        vols = {'HIGH_VOL': [], 'LOW_VOL': [], 'MID_VOL': []}
        
        for s in signals:
            idx = s.get('idx', -1)
            if 0 <= idx < len(adx):
                adx_val = adx[idx]
                if adx_val > 25:
                    regimes['TRENDING'].append(s)
                elif adx_val < 15:
                    regimes['SIDEWAYS'].append(s)
                else:
                    regimes['MIXED'].append(s)
                
                atr_ratio = atr[idx] / closes[idx] if closes[idx] else 0
                if atr_ratio > 0.01:
                    vols['HIGH_VOL'].append(s)
                elif atr_ratio < 0.005:
                    vols['LOW_VOL'].append(s)
                else:
                    vols['MID_VOL'].append(s)
        
        lines.append("  ADX Regime:")
        print("    ADX Regime:")
        for regime, sigs in regimes.items():
            stats = compute_stats(sigs)
            l = f"    {regime}: n={stats['n']} WR={stats['wr']}% PF={stats['pf']}"
            lines.append(f"  {l}")
            print(f"    {l}")
        
        lines.append("  Volatility Regime (ATR/close):")
        print("    Volatility Regime:")
        for vol, sigs in vols.items():
            stats = compute_stats(sigs)
            l = f"    {vol}: n={stats['n']} WR={stats['wr']}% PF={stats['pf']}"
            lines.append(f"  {l}")
            print(f"    {l}")
    
    lines.append("\n")
    output = '\n'.join(lines)
    if save:
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_5_regime.txt', 'w') as f:
            f.write(output)
    return lines

# ═══════════════════════════════════════════════════════════════════════════════
# TASK 6: Simple Monte Carlo (VWAP)
# ═══════════════════════════════════════════════════════════════════════════════

def task6_monte_carlo(save=True):
    """Test if VWAP WR is statistically significant."""
    print("\n" + "="*70)
    print("  TASK 6: MONTE CARLO (VWAP on GZ)")
    print("="*70)
    
    lines = ["=== MONTE CARLO: VWAP on GZ ===\n"]
    
    data = load_and_prepare('vwap', 'GZ', days=365)
    if not data:
        lines.append("No data for GZ\n")
        print("  No data for GZ")
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_6_monte_carlo.txt', 'w') as f:
            f.write('\n'.join(lines))
        return lines
    
    config = {'dev_thresh': 2.0, 'horizon': 12, 'vwap_window': 20, 'atr_period': 14}
    signals = run_strategy('vwap', 'GZ', data, config)
    
    if not signals:
        lines.append("No signals generated\n")
        print("  No signals generated")
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_6_monte_carlo.txt', 'w') as f:
            f.write('\n'.join(lines))
        return lines
    
    returns = [s['return_pct'] for s in signals]
    actual_wr = sum(1 for r in returns if r > 0) / len(returns) * 100
    n = len(returns)
    
    lines.append(f"  Actual signals: {n}")
    lines.append(f"  Actual WR: {actual_wr:.1f}%\n")
    print(f"  Actual signals: {n}, WR: {actual_wr:.1f}%")
    
    # Monte Carlo: test against null hypothesis of 50% WR (random coin flip)
    N = 10000
    count = 0
    for _ in range(N):
        wins = sum(1 for _ in range(n) if random.random() < 0.5)
        wr = wins / n * 100
        if wr >= actual_wr:
            count += 1
    
    p_value = count / N
    significant = p_value < 0.05
    
    lines.append(f"  Monte Carlo runs: {N}")
    lines.append(f"  Runs with WR >= actual: {count}")
    lines.append(f"  p-value: {p_value:.4f}")
    lines.append(f"  Result: {'SIGNIFICANT' if significant else 'NOT SIGNIFICANT'} (threshold: p<0.05)")
    lines.append("")
    
    print(f"  p-value: {p_value:.4f} ({'SIGNIFICANT' if significant else 'NOT SIGNIFICANT'})")
    
    output = '\n'.join(lines)
    if save:
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_6_monte_carlo.txt', 'w') as f:
            f.write(output)
    return p_value, actual_wr

# ═══════════════════════════════════════════════════════════════════════════════
# TASK 7: Slippage/Commission Modeling
# ═══════════════════════════════════════════════════════════════════════════════

def apply_slippage(signal, tick_size, is_long):
    """Apply 1 tick slippage to entry and exit."""
    s = dict(signal)
    if is_long:
        s['entry'] = s['entry'] + tick_size
        s['exit'] = s['exit'] - tick_size
        s['return_pct'] = (s['exit'] - s['entry']) / s['entry'] * 100
    else:
        s['entry'] = s['entry'] - tick_size
        s['exit'] = s['exit'] + tick_size
        s['return_pct'] = (s['entry'] - s['exit']) / s['entry'] * 100
    return s

def task7_slippage(save=True):
    """Model 1 tick slippage impact."""
    print("\n" + "="*70)
    print("  TASK 7: SLIPPAGE/COMMISSION MODELING")
    print("="*70)
    
    lines = ["=== SLIPPAGE/COMMISSION ANALYSIS ===\n"]
    
    tests = [
        ('vwap', 'GZ', 'VWAP on GZ', 0.01, {'dev_thresh': 2.0, 'horizon': 12, 'vwap_window': 20, 'atr_period': 14}),
        ('ob', 'SBERF', 'OB on SBERF', 1.0, None),
    ]
    
    for skey, ticker, label, tick_size, config in tests:
        print(f"\n  --- {label} (tick={tick_size}) ---")
        lines.append(f"\n=== {label} (tick={tick_size}) ===\n")
        
        data = load_and_prepare(skey, ticker, days=365)
        if not data:
            lines.append("  No data\n")
            continue
        
        c = config if config else STRATEGIES[skey]['config']
        signals = run_strategy(skey, ticker, data, c)
        
        if not signals:
            lines.append("  No signals\n")
            continue
        
        # Base stats
        base_stats = compute_stats(signals)
        lines.append(f"  Base (no slippage): WR={base_stats['wr']}% PF={base_stats['pf']} n={base_stats['n']}")
        print(f"    Base: WR={base_stats['wr']}% PF={base_stats['pf']} n={base_stats['n']}")
        
        # Apply slippage
        slipped_signals = []
        for s in signals:
            is_long = s['direction'] == 'LONG'
            slipped = apply_slippage(s, tick_size, is_long)
            slipped_signals.append(slipped)
        
        slip_stats = compute_stats(slipped_signals)
        wr_diff = slip_stats['wr'] - base_stats['wr']
        pf_diff = slip_stats['pf'] - base_stats['pf']
        
        lines.append(f"  With 1-tick slippage: WR={slip_stats['wr']}% PF={slip_stats['pf']} n={slip_stats['n']}")
        lines.append(f"  Change: WR={wr_diff:+.1f}% PF={pf_diff:+.2f}")
        print(f"    With slippage: WR={slip_stats['wr']}% PF={slip_stats['pf']}")
        print(f"    Change: WR={wr_diff:+.1f}% PF={pf_diff:+.2f}")
        lines.append("")
    
    output = '\n'.join(lines)
    if save:
        with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_7_slippage.txt', 'w') as f:
            f.write(output)
    return lines

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("="*70)
    print("  DEEP TESTING SUITE — TQA-MOEX")
    print("="*70)
    
    # Task 1: Walk-Forward (heaviest, run first)
    print("\n\n" + "█"*70)
    print("  █ TASK 1: WALK-FORWARD VALIDATION")
    print("█"*70)
    wf_results = task1_walkforward(save=True)
    
    # Task 2: Return Cross-Check
    print("\n\n" + "█"*70)
    print("  █ TASK 2: RETURN CROSS-CHECK")
    print("█"*70)
    err_count = task2_return_crosscheck(save=True)
    
    # Task 3: Parameter Sensitivity
    print("\n\n" + "█"*70)
    print("  █ TASK 3: PARAMETER SENSITIVITY")
    print("█"*70)
    task3_sensitivity(save=True)
    
    # Task 4: Signal Overlap
    print("\n\n" + "█"*70)
    print("  █ TASK 4: SIGNAL OVERLAP")
    print("█"*70)
    task4_signal_overlap(save=True)
    
    # Task 5: Regime Analysis
    print("\n\n" + "█"*70)
    print("  █ TASK 5: MARKET REGIME ANALYSIS")
    print("█"*70)
    task5_regime_analysis(save=True)
    
    # Task 6: Monte Carlo
    print("\n\n" + "█"*70)
    print("  █ TASK 6: MONTE CARLO")
    print("█"*70)
    p_val, actual_wr = task6_monte_carlo(save=True)
    
    # Task 7: Slippage
    print("\n\n" + "█"*70)
    print("  █ TASK 7: SLIPPAGE MODELING")
    print("█"*70)
    task7_slippage(save=True)
    
    # Summary
    print("\n\n" + "█"*70)
    print("  █ SUMMARY")
    print("█"*70)
    
    mc_line = f"Task 6 (Monte Carlo): p-value={p_val:.4f} ({'SIGNIFICANT' if p_val < 0.05 else 'NOT SIGNIFICANT'})" if 'p_val' in dir() and 'p_val' in locals() else "Task 6: See results"
    
    summary_lines = [
        "=== DEEP TESTING SUMMARY ===\n",
        f"Task 1 (Walk-Forward): Completed {len(wf_results)} strategy-ticker combinations" if wf_results else "Task 1 (Walk-Forward): No results",
        "",
        f"Task 2 (Return Cross-Check): {'PASS (0 errors)' if err_count == 0 else f'FAIL ({err_count} errors)'}",
        "",
        "Task 3 (Parameter Sensitivity): See deep_test_3_sensitivity.txt",
        "",
        "Task 4 (Signal Overlap): See deep_test_4_overlap.txt",
        "",
        "Task 5 (Market Regime): See deep_test_5_regime.txt",
        "",
        mc_line,
        "",
        "Task 7 (Slippage): See deep_test_7_slippage.txt",
        "",
        "="*50,
    ]
    
    summary_lines.append("")
    
    summary = '\n'.join(summary_lines)
    with open('/home/user/projects/TQA-MOEX/docs/backtest/deep_test_summary.txt', 'w') as f:
        f.write(summary)
    print(summary)
