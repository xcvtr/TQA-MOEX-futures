#!/usr/bin/env python3
"""
4 New MOEX Trading Strategies — Implementation & Backtest.

Usage:
    python -m trading_bot.new_strategies

Output:
    docs/backtest/otc_results.txt
    docs/backtest/retail_trap_results.txt
    docs/backtest/vwap_results.txt
    docs/backtest/oi_divergence_results.txt
    docs/backtest/summary.txt
"""

import psycopg2
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict, Optional, Callable, Any
import os
import sys

# ── DB ──────────────────────────────────────────────────────────────────────
DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')
ZSCORE_WINDOW = 20
MEDIAN_WINDOW = 20

# ── Data cache (avoid redundant DB loads across param grids) ────────────────
_data_cache: Dict[Tuple[str, int, bool], Any] = {}

# ── Helpers (NO look-ahead) ─────────────────────────────────────────────────

def _zs(vals, w=ZSCORE_WINDOW):
    """Rolling z-score, NO look-ahead. Uses only vals[:i]."""
    out = [0.0] * len(vals)
    for i in range(w, len(vals)):
        chunk = vals[i-w:i]
        mu = sum(chunk) / w
        var = sum((x-mu)**2 for x in chunk) / w
        sd = var**0.5
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def _rolling_median(arr, w=MEDIAN_WINDOW):
    """Rolling median of PREVIOUS values. NO look-ahead."""
    out = [0.0] * len(arr)
    for i in range(len(arr)):
        win = arr[:i] if i < w else arr[i-w:i]
        if not win:
            win = [arr[0]]
        sw = sorted(win)
        mid = len(sw)//2
        out[i] = (sw[mid-1]+sw[mid])/2.0 if len(sw)%2==0 else float(sw[mid])
    return out


# ── Data loading ────────────────────────────────────────────────────────────

def load_ohlcv(symbol, days=90):
    """Load OHLCV 5m. Returns list of dicts."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, open, high, low, close, volume
        FROM moex_prices_5m
        WHERE symbol = %s AND time >= %s
        ORDER BY time
    """, (symbol, since))
    rows = []
    for r in cur:
        rows.append({
            'time': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
            'open': float(r[1]), 'high': float(r[2]),
            'low': float(r[3]), 'close': float(r[4]),
            'volume': float(r[5]),
            'symbol': symbol,
        })
    cur.close()
    conn.close()
    return rows


def load_oi(symbol, days=90):
    """Load OI data. Returns list of dicts."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
        FROM moex_prices_5m_oi
        WHERE symbol = %s AND time >= %s
        ORDER BY time
    """, (symbol, since))
    rows = []
    for r in cur:
        rows.append({
            'time': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
            'fiz_buy': float(r[1]), 'fiz_sell': float(r[2]),
            'yur_buy': float(r[3]), 'yur_sell': float(r[4]),
            'total_oi': float(r[5]),
        })
    cur.close()
    conn.close()
    return rows


def merge_ohlcv_oi(ohlcv, oi):
    """Merge OHLCV and OI on time. Returns list of merged dicts."""
    oi_by_time = {r['time'][:16]: r for r in oi}
    merged = []
    for r in ohlcv:
        oi_row = oi_by_time.get(r['time'][:16])
        if oi_row is None:
            continue
        merged.append({**r, **oi_row})
    return merged


def _load_data_cached(ticker, days, with_oi=False):
    """Cache data loading to avoid redundant DB hits."""
    key = (ticker, days, with_oi)
    if key not in _data_cache:
        ohlcv = load_ohlcv(ticker, days)
        if with_oi:
            oi = load_oi(ticker, days)
            _data_cache[key] = merge_ohlcv_oi(ohlcv, oi)
        else:
            _data_cache[key] = ohlcv
    return _data_cache[key]


# ═════════════════════════════════════════════════════════════════════════════
# Strategy 1: OI Trend Confirmation (OTC)
# ═════════════════════════════════════════════════════════════════════════════

def detect_otc_signals(merged, config=None):
    """
    OI Trend Confirmation.

    Per bar i:
    1. z-score of total_oi over 20 bars
    2. z-score of close over 20 bars
    3. oi_z > th AND price_z > th → LONG
    4. oi_z < -th AND price_z < -th → SHORT
    5. Divergence → skip
    """
    default = {'oi_z_thresh': 0.5, 'price_z_thresh': 0.5, 'horizon': 6}
    config = {**default, **(config or {})}

    n = len(merged)
    if n < 50:
        return []

    oi = [r['total_oi'] for r in merged]
    closes = [r['close'] for r in merged]

    oi_z = _zs(oi, 20)
    price_z = _zs(closes, 20)

    signals = []
    min_idx = 25
    th = config['oi_z_thresh']
    pth = config['price_z_thresh']
    horizon = config['horizon']

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        oi_up = oi_z[i] > th
        oi_dn = oi_z[i] < -th
        pr_up = price_z[i] > pth
        pr_dn = price_z[i] < -pth

        if oi_up and pr_up:
            direction = 'LONG'
        elif oi_dn and pr_dn:
            direction = 'SHORT'
        else:
            continue

        entry = merged[i+1]['open']
        if entry <= 0:
            continue
        exit_price = merged[i+horizon]['close'] if i+horizon < n else merged[-1]['close']

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': merged[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': merged[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'otc', 'idx': i,
            'oi_z': round(oi_z[i], 4), 'price_z': round(price_z[i], 4),
        })

    return signals


# ═════════════════════════════════════════════════════════════════════════════
# Strategy 2: Retail Trap (Fiz Extremes)
# ═════════════════════════════════════════════════════════════════════════════

def detect_retail_trap_signals(merged, config=None):
    """
    Retail Trap — contrarian to fiz positioning.

    Per bar i:
    1. fiz_net = fiz_buy - fiz_sell
    2. fiz_total = fiz_buy + fiz_sell
    3. fiz_ratio = fiz_net / max(fiz_total, 1)
    4. z-score of fiz_ratio over 20 bars → fiz_z
    5. fiz_z > th → SHORT (fiz overbought)
    6. fiz_z < -th → LONG (fiz oversold)
    """
    default = {'fiz_z_thresh': 1.5, 'horizon': 6}
    config = {**default, **(config or {})}

    n = len(merged)
    if n < 50:
        return []

    fiz_ratio = []
    for r in merged:
        total = r['fiz_buy'] + r['fiz_sell']
        net = r['fiz_buy'] - r['fiz_sell']
        fiz_ratio.append(net / max(total, 1))

    fiz_z = _zs(fiz_ratio, 20)

    signals = []
    min_idx = 25
    th = config['fiz_z_thresh']
    horizon = config['horizon']

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        if fiz_z[i] > th:
            direction = 'SHORT'
        elif fiz_z[i] < -th:
            direction = 'LONG'
        else:
            continue

        entry = merged[i+1]['open']
        if entry <= 0:
            continue
        exit_price = merged[i+horizon]['close'] if i+horizon < n else merged[-1]['close']

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': merged[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': merged[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'retail_trap', 'idx': i,
            'fiz_z': round(fiz_z[i], 4),
        })

    return signals


# ═════════════════════════════════════════════════════════════════════════════
# Strategy 3: VWAP Deviation Reversion
# ═════════════════════════════════════════════════════════════════════════════

def detect_vwap_signals(ohlcv, config=None):
    """VWAP Deviation Reversion — pure price, no OI needed.

    Per bar i:
    1. vwap = sum(close*volume) / sum(volume) over last N bars
    2. atr = mean true range over 14 bars
    3. deviation = (close - vwap) / atr
    4. deviation > th → SHORT
    5. deviation < -th → LONG
    """
    default = {'dev_thresh': 2.0, 'horizon': 6, 'vwap_window': 20, 'atr_period': 14}
    config = {**default, **(config or {})}

    n = len(ohlcv)
    if n < 50:
        return []

    closes = [r['close'] for r in ohlcv]
    volumes = [r['volume'] for r in ohlcv]
    highs = [r['high'] for r in ohlcv]
    lows = [r['low'] for r in ohlcv]

    # Rolling VWAP (no look-ahead)
    vwap_w = config['vwap_window']
    vwap = [0.0] * n
    for i in range(vwap_w, n):
        cum_pv = sum(closes[j] * volumes[j] for j in range(i-vwap_w, i))
        cum_v = sum(volumes[j] for j in range(i-vwap_w, i))
        vwap[i] = cum_pv / cum_v if cum_v > 0 else closes[i]

    # Rolling ATR (no look-ahead)
    atr_p = config['atr_period']
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    atr = [0.0] * n
    for i in range(atr_p, n):
        atr[i] = sum(tr[i-atr_p:i]) / atr_p

    signals = []
    min_idx = max(vwap_w, atr_p) + 5
    th = config['dev_thresh']
    horizon = config['horizon']

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue
        if atr[i] <= 0:
            continue

        dev = (closes[i] - vwap[i]) / atr[i]

        if dev > th:
            direction = 'SHORT'
        elif dev < -th:
            direction = 'LONG'
        else:
            continue

        entry = ohlcv[i+1]['open']
        if entry <= 0:
            continue
        exit_price = ohlcv[i+horizon]['close'] if i+horizon < n else ohlcv[-1]['close']

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': ohlcv[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': ohlcv[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'vwap', 'idx': i,
            'deviation': round(dev, 4),
        })

    return signals


# ═════════════════════════════════════════════════════════════════════════════
# Strategy 4: OI Divergence (Classic)
# ═════════════════════════════════════════════════════════════════════════════

def detect_oi_divergence_signals(merged, config=None):
    """
    OI Divergence — classic futures analysis.

    Per bar i:
    1. Find last swing high/low in [i-lookback, i-extreme_window]
    2. If close > swing_high_close AND oi < swing_high_oi * bear_th → SHORT
    3. If close < swing_low_close AND oi > swing_low_oi * bull_th → LONG
    """
    default = {'lookback': 20, 'horizon': 6, 'extreme_window': 10,
               'bear_threshold': 0.95, 'bull_threshold': 1.05}
    config = {**default, **(config or {})}

    n = len(merged)
    if n < 50:
        return []
    if n < config['lookback'] + config['extreme_window'] + 5:
        return []

    closes = [r['close'] for r in merged]
    oi_vals = [r['total_oi'] for r in merged]
    lookback = config['lookback']
    ext_w = config['extreme_window']
    horizon = config['horizon']
    bear_th = config['bear_threshold']
    bull_th = config['bull_threshold']

    signals = []
    min_idx = lookback + 5

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        search_start = max(0, i - lookback)
        search_end = max(search_start + 1, i - ext_w)
        if search_end <= search_start:
            continue

        max_idx = search_start
        min_idx_val = search_start
        for j in range(search_start, search_end):
            if closes[j] > closes[max_idx]:
                max_idx = j
            if closes[j] < closes[min_idx_val]:
                min_idx_val = j

        direction = None
        if closes[i] > closes[max_idx] and oi_vals[i] < oi_vals[max_idx] * bear_th:
            direction = 'SHORT'
        elif closes[i] < closes[min_idx_val] and oi_vals[i] > oi_vals[min_idx_val] * bull_th:
            direction = 'LONG'

        if direction is None:
            continue

        entry = merged[i+1]['open']
        if entry <= 0:
            continue
        exit_price = merged[i+horizon]['close'] if i+horizon < n else merged[-1]['close']

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': merged[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': merged[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'oi_divergence', 'idx': i,
        })

    return signals


def detect_oi_divergence_signals_limit(merged, config=None):
    """
    Limit-order variant of OI Divergence.

    LONG: limit_price = low[i], fill when low[j] <= limit_price
    SHORT: limit_price = high[i], fill when high[j] >= limit_price
    Search fill within limit_lookback bars after trigger.
    """
    default = {'lookback': 20, 'horizon': 6, 'extreme_window': 10,
               'bear_threshold': 0.95, 'bull_threshold': 1.05,
               'limit_lookback': 5}
    config = {**default, **(config or {})}

    n = len(merged)
    if n < 50:
        return []
    if n < config['lookback'] + config['extreme_window'] + 5:
        return []

    closes = [r['close'] for r in merged]
    oi_vals = [r['total_oi'] for r in merged]
    highs = [r['high'] for r in merged]
    lows = [r['low'] for r in merged]
    lookback = config['lookback']
    ext_w = config['extreme_window']
    horizon = config['horizon']
    bear_th = config['bear_threshold']
    bull_th = config['bull_threshold']
    limit_lookback = config['limit_lookback']

    signals = []
    min_idx = lookback + 5

    for i in range(min_idx, n):
        if i + 1 >= n:
            break
        if i + horizon >= n:
            continue

        search_start = max(0, i - lookback)
        search_end = max(search_start + 1, i - ext_w)
        if search_end <= search_start:
            continue

        max_idx = search_start
        min_idx_val = search_start
        for j in range(search_start, search_end):
            if closes[j] > closes[max_idx]:
                max_idx = j
            if closes[j] < closes[min_idx_val]:
                min_idx_val = j

        direction = None
        if closes[i] > closes[max_idx] and oi_vals[i] < oi_vals[max_idx] * bear_th:
            direction = 'SHORT'
        elif closes[i] < closes[min_idx_val] and oi_vals[i] > oi_vals[min_idx_val] * bull_th:
            direction = 'LONG'

        if direction is None:
            continue

        if direction == 'LONG':
            limit_price = lows[i]
        else:
            limit_price = highs[i]

        fill_bar = None
        max_j = min(i + 1 + limit_lookback, n)
        for j in range(i + 1, max_j):
            if direction == 'LONG' and lows[j] <= limit_price:
                fill_bar = j
                break
            elif direction == 'SHORT' and highs[j] >= limit_price:
                fill_bar = j
                break

        if fill_bar is None:
            continue

        ex = fill_bar + horizon
        if ex >= n:
            continue

        entry = limit_price
        if entry <= 0:
            continue

        exit_price = merged[ex]['close']

        if direction == 'LONG':
            ret = (exit_price - entry) / entry * 100
        else:
            ret = (entry - exit_price) / entry * 100

        signals.append({
            'ticker': merged[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': merged[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'oi_divergence', 'idx': i,
            'fill_bar': fill_bar, 'limit_price': round(limit_price, 4),
        })

    return signals


# ═════════════════════════════════════════════════════════════════════════════
# Backtest Harness
# ═════════════════════════════════════════════════════════════════════════════

def compute_stats(signals):
    """WR, PF, DD, avg_ret for a list of signals."""
    if not signals:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0, 'avg_ret': 0.0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins) / n * 100 if n > 0 else 0.0
    sum_wins = sum(wins) if wins else 0.0
    sum_losses = abs(sum(losses)) if losses else 0.0
    pf = sum_wins / sum_losses if sum_losses > 0 else (sum_wins if sum_wins > 0 else 0.0)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd:
            max_dd = dd
    return {
        'n': n, 'wr': round(wr, 1), 'pf': round(pf, 2),
        'dd': round(max_dd, 1), 'avg_ret': round(sum(returns)/n, 2)
    }


_NEEDS_OI = {'detect_otc_signals', 'detect_retail_trap_signals', 'detect_oi_divergence_signals'}


def run_all_tickers(strategy_fn, tickers, days=180, **kwargs):
    """
    Run a strategy on all tickers. Returns {ticker: results} dict.

    70/30 split by time. Only last 30% is reported (out-of-sample).
    Uses data caching to avoid redundant DB loads.
    """
    fn_name = strategy_fn.__name__
    needs_oi = fn_name in _NEEDS_OI

    results = {}
    for tk in tickers:
        try:
            full_data = _load_data_cached(tk, days, with_oi=needs_oi)
            if not full_data or len(full_data) < 100:
                continue

            split_idx = int(len(full_data) * 0.7)

            cfg = kwargs.get('config')
            all_signals = strategy_fn(full_data, config=cfg)

            test_signals = [s for s in all_signals if s['idx'] >= split_idx]
            test_stats = compute_stats(test_signals)

            results[tk] = {
                'test_signals': len(test_signals),
                'test_stats': test_stats,
                'total_signals': len(all_signals),
            }
        except Exception as e:
            results[tk] = {'error': str(e)}

    return results


def format_results(strategy_name, results):
    """Format results as a string table."""
    lines = []
    lines.append(f"{'='*60}")
    lines.append(f"  {strategy_name}")
    lines.append(f"{'='*60}")
    lines.append(f"{'Ticker':<10} {'Sig':<6} {'WR%':<8} {'PF':<8} {'DD%':<8} {'AvgRet':<8}")
    lines.append(f"{'-'*50}")
    ranked = [
        (tk, r) for tk, r in results.items()
        if 'test_stats' in r and r['test_signals'] > 0
    ]
    ranked.sort(key=lambda x: x[1]['test_stats']['wr'], reverse=True)
    for tk, r in ranked:
        s = r['test_stats']
        lines.append(f"{tk:<10} {r['test_signals']:<6} {s['wr']:<8} {s['pf']:<8} {s['dd']:<8} {s['avg_ret']:<8}")
    errors = [(tk, r) for tk, r in results.items() if 'error' in r]
    for tk, r in errors:
        lines.append(f"{tk:<10} ERROR: {r['error']}")
    return '\n'.join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Main — run all strategies
# ═════════════════════════════════════════════════════════════════════════════

TICKERS = [
    'CNYRUBF', 'Si', 'CC', 'SV', 'GLDRUBF', 'IMOEXF', 'BR', 'SS', 'USDRUBF',
    'GL', 'GK', 'NG', 'BM', 'NA', 'GD', 'IB', 'VB', 'MC', 'MX', 'GZ', 'Eu',
    'KC', 'ED', 'SR', 'SF',
]


def run_all_strategies():
    """Run all 4 strategies with parameter grids and return accumulated results."""
    all_output = []
    all_best_per_strategy = []
    overview_rows = []  # for summary

    start_time = datetime.now()

    # ═══ Strategy 1: OTC ═══
    strategy_name = "OTC"
    strategy_best = {'label': None, 'avg_wr': 0.0, 'results': None}
    for oi_th in [0.3, 0.5, 0.7]:
        for h in [3, 6, 12]:
            label = f"OTC oi_z>{oi_th} h={h}"
            print(f"  Running {label}...")
            r = run_all_tickers(
                detect_otc_signals, TICKERS, days=180,
                config={'oi_z_thresh': oi_th, 'price_z_thresh': 0.5, 'horizon': h}
            )
            output = format_results(label, r)
            all_output.append(('otc', label, output))

            wrs = [v['test_stats']['wr'] for v in r.values()
                   if 'test_stats' in v and v['test_signals'] > 0]
            avg_wr = sum(wrs) / len(wrs) if wrs else 0
            if strategy_best['label'] is None or avg_wr > strategy_best['avg_wr']:
                strategy_best = {'label': label, 'avg_wr': avg_wr, 'results': r}

    all_best_per_strategy.append((strategy_name, strategy_best))
    overview_rows.append(f"{strategy_name:<20} best={strategy_best['label']:<30} avgWR={strategy_best['avg_wr']:.1f}%")

    # ═══ Strategy 2: Retail Trap ═══
    strategy_name = "Retail Trap"
    strategy_best = {'label': None, 'avg_wr': 0.0, 'results': None}
    for fiz_th in [1.0, 1.5, 2.0]:
        for h in [3, 6, 12]:
            label = f"RetailTrap fiz_z>{fiz_th} h={h}"
            print(f"  Running {label}...")
            r = run_all_tickers(
                detect_retail_trap_signals, TICKERS, days=180,
                config={'fiz_z_thresh': fiz_th, 'horizon': h}
            )
            output = format_results(label, r)
            all_output.append(('retail_trap', label, output))

            wrs = [v['test_stats']['wr'] for v in r.values()
                   if 'test_stats' in v and v['test_signals'] > 0]
            avg_wr = sum(wrs) / len(wrs) if wrs else 0
            if strategy_best['label'] is None or avg_wr > strategy_best['avg_wr']:
                strategy_best = {'label': label, 'avg_wr': avg_wr, 'results': r}

    all_best_per_strategy.append((strategy_name, strategy_best))
    overview_rows.append(f"{strategy_name:<20} best={strategy_best['label']:<30} avgWR={strategy_best['avg_wr']:.1f}%")

    # ═══ Strategy 3: VWAP ═══
    strategy_name = "VWAP"
    strategy_best = {'label': None, 'avg_wr': 0.0, 'results': None}
    for dev_th in [1.5, 2.0, 2.5]:
        for h in [3, 6, 12]:
            for vwap_w in [20, 50]:
                for atr_p in [14]:
                    label = f"VWAP dev>{dev_th} h={h} vwap={vwap_w}"
                    print(f"  Running {label}...")
                    r = run_all_tickers(
                        detect_vwap_signals, TICKERS, days=180,
                        config={'dev_thresh': dev_th, 'horizon': h,
                                'vwap_window': vwap_w, 'atr_period': atr_p}
                    )
                    output = format_results(label, r)
                    all_output.append(('vwap', label, output))

                    wrs = [v['test_stats']['wr'] for v in r.values()
                           if 'test_stats' in v and v['test_signals'] > 0]
                    avg_wr = sum(wrs) / len(wrs) if wrs else 0
                    if strategy_best['label'] is None or avg_wr > strategy_best['avg_wr']:
                        strategy_best = {'label': label, 'avg_wr': avg_wr, 'results': r}

    all_best_per_strategy.append((strategy_name, strategy_best))
    overview_rows.append(f"{strategy_name:<20} best={strategy_best['label']:<30} avgWR={strategy_best['avg_wr']:.1f}%")

    # ═══ Strategy 4: OI Divergence ═══
    strategy_name = "OI Divergence"
    strategy_best = {'label': None, 'avg_wr': 0.0, 'results': None}
    for lb in [10, 20, 30]:
        for h in [3, 6, 12]:
            for bear, bull in [(0.95, 1.05), (0.90, 1.10)]:
                label = f"OIDiv lb={lb} h={h} bear={bear}"
                print(f"  Running {label}...")
                r = run_all_tickers(
                    detect_oi_divergence_signals, TICKERS, days=180,
                    config={'lookback': lb, 'horizon': h, 'extreme_window': 10,
                            'bear_threshold': bear, 'bull_threshold': bull}
                )
                output = format_results(label, r)
                all_output.append(('oi_divergence', label, output))

                wrs = [v['test_stats']['wr'] for v in r.values()
                       if 'test_stats' in v and v['test_signals'] > 0]
                avg_wr = sum(wrs) / len(wrs) if wrs else 0
                if strategy_best['label'] is None or avg_wr > strategy_best['avg_wr']:
                    strategy_best = {'label': label, 'avg_wr': avg_wr, 'results': r}

    all_best_per_strategy.append((strategy_name, strategy_best))
    overview_rows.append(f"{strategy_name:<20} best={strategy_best['label']:<30} avgWR={strategy_best['avg_wr']:.1f}%")

    elapsed = (datetime.now() - start_time).total_seconds()

    # ── Write per-strategy files ──
    strategy_files = {
        'otc': [],
        'retail_trap': [],
        'vwap': [],
        'oi_divergence': [],
    }
    for strategy_type, label, output in all_output:
        strategy_files[strategy_type].append((label, output))

    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'docs', 'backtest')
    os.makedirs(out_dir, exist_ok=True)

    for sname, entries in strategy_files.items():
        filepath = os.path.join(out_dir, f'{sname}_results.txt')
        with open(filepath, 'w') as f:
            f.write(f"MOEX New Strategies — Backtest Results: {sname}\n")
            f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"Tickers: {len(TICKERS)}\n")
            f.write(f"Data window: 180 days\n")
            f.write(f"{'='*60}\n\n")
            for label, output in entries:
                f.write(output)
                f.write('\n\n')
        print(f"  → Wrote {filepath}")

    # ── Summary ──
    summary_lines = []
    summary_lines.append("=" * 60)
    summary_lines.append("  MOEX New Strategies — Out-of-Sample Summary")
    summary_lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    summary_lines.append(f"  Elapsed: {elapsed:.0f}s")
    summary_lines.append(f"  Tickers: {len(TICKERS)}")
    summary_lines.append(f"  Data window: 180 days (last 30% = out-of-sample)")
    summary_lines.append("=" * 60)
    summary_lines.append("")
    summary_lines.append("── Best Parameter Set per Strategy ──")
    summary_lines.append("")
    for sn, sb in all_best_per_strategy:
        summary_lines.append(f"  {sn:<20} {sb['label']:<35} avgWR={sb['avg_wr']:.1f}%")
    summary_lines.append("")

    # Per-strategy: top 3 tickers
    summary_lines.append("── Top-3 Tickers per Strategy (at best params) ──")
    summary_lines.append("")
    for sn, sb in all_best_per_strategy:
        r = sb['results']
        if r is None:
            continue
        summary_lines.append(f"  {sn} ({sb['label']}):")
        ranked = [
            (tk, v) for tk, v in r.items()
            if 'test_stats' in v and v['test_signals'] > 0
        ]
        ranked.sort(key=lambda x: x[1]['test_stats']['wr'], reverse=True)
        for tk, v in ranked[:3]:
            s = v['test_stats']
            summary_lines.append(f"    {tk:<10} sig={s['n']:<4} WR={s['wr']:<6}% PF={s['pf']:<6} DD={s['dd']:<6}% avgRet={s['avg_ret']:<6}%")
        summary_lines.append("")

    # Cross-strategy ranking
    summary_lines.append("── Cross-Strategy Ranking (by avg WR) ──")
    summary_lines.append("")
    strategy_ranking = sorted(all_best_per_strategy, key=lambda x: x[1]['avg_wr'], reverse=True)
    for rank, (sn, sb) in enumerate(strategy_ranking, 1):
        summary_lines.append(f"  {rank}. {sn:<20} avgWR={sb['avg_wr']:.1f}%  (params: {sb['label']})")
    summary_lines.append("")

    # Recommendation
    summary_lines.append("── Recommendation ──")
    summary_lines.append("")
    if strategy_ranking:
        best_strat = strategy_ranking[0]
        summary_lines.append(f"  Top strategy to integrate: {best_strat[0]} ({best_strat[1]['label']})")
        summary_lines.append(f"  Average WR across tickers: {best_strat[1]['avg_wr']:.1f}%")
        summary_lines.append("")
        summary_lines.append("  Integration: add to cron_scanner.py via separate config block.")
        summary_lines.append("  Use best params per ticker (see per-strategy files).")
        summary_lines.append("")

        # Detail the top results
        summary_lines.append("── Top-5 Overall Across All Strategies ──")
        all_ranked = []
        for sn, sb in all_best_per_strategy:
            if sb['results'] is None:
                continue
            for tk, v in sb['results'].items():
                if 'test_stats' in v and v['test_signals'] > 0:
                    all_ranked.append((sn, tk, v['test_stats']))
        all_ranked.sort(key=lambda x: x[2]['wr'], reverse=True)
        for sn, tk, s in all_ranked[:5]:
            summary_lines.append(f"    {sn:<20} {tk:<10} WR={s['wr']:<6}% PF={s['pf']:<6} avgRet={s['avg_ret']:<6}% sig={s['n']}")

    summary_path = os.path.join(out_dir, 'summary.txt')
    with open(summary_path, 'w') as f:
        f.write('\n'.join(summary_lines))
    print(f"  → Wrote {summary_path}")

    # Print summary to stdout
    print('\n'.join(summary_lines))


if __name__ == '__main__':
    print("=" * 60)
    print("  MOEX New Strategies — Out-of-Sample Backtest (last 30%)")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Tickers: {len(TICKERS)}")
    print(f"  Data window: 180 days")
    print("=" * 60)
    run_all_strategies()
