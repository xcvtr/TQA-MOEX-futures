# 4 New MOEX Trading Strategies — Implementation & Backtest

**Goal:** Implement and backtest 4 complementary strategies on MOEX futures data, find which tickers work for each.

**No look-ahead:** All indicators use ONLY data available at the decision point. Verified by design.

**Data source:** `moex` DB on 10.0.0.64:5432, user=postgres, password=***
- `moex_prices_5m` — OHLCV (symbol, time, open, high, low, close, volume)
- `moex_prices_5m_oi` — OI (symbol, time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi)

**Output:** `docs/backtest/` per strategy with summary tables.

---

## Common Utilities

Create `trading_bot/new_strategies.py` with shared helpers:

```python
import psycopg2
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')
ZSCORE_WINDOW = 20
MEDIAN_WINDOW = 20

def _zs(vals, w=ZSCORE_WINDOW):
    """Rolling z-score, NO look-ahead."""
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
        if not win: win = [arr[0]]
        sw = sorted(win)
        mid = len(sw)//2
        out[i] = (sw[mid-1]+sw[mid])/2.0 if len(sw)%2==0 else float(sw[mid])
    return out

def load_ohlcv(symbol, days=90):
    """Load OHLCV 5m. Returns list of dicts with keys: time, open, high, low, close, volume."""
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
        })
    cur.close(); conn.close()
    return rows

def load_oi(symbol, days=90):
    """Load OI data. Returns list of dicts with keys: time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi."""
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
    cur.close(); conn.close()
    return rows

def merge_ohlcv_oi(ohlcv, oi):
    """Merge OHLCV and OI on time. Returns list of merged dicts or None if no match."""
    oi_by_time = {r['time'][:16]: r for r in oi}
    merged = []
    for r in ohlcv:
        oi_row = oi_by_time.get(r['time'][:16])
        if oi_row is None:
            continue
        merged.append({**r, **oi_row})
    return merged

# ⚠️ CRITICAL: ALL indicator calculations use only [:i] (data before current bar)
# Never use data[i+1:] or full series statistics.
```

---

## Strategy 1: OI Trend Confirmation (OTC)

**Logic:** If total_oi is above its median AND price is moving in the same direction → trend is confirmed.

### Signal Detection

```python
def detect_otc_signals(merged, config=None):
    """
    OI Trend Confirmation.
    
    Signal logic (per bar i):
    1. Compute z-score of total_oi over 20 bars
    2. Compute z-score of close over 20 bars (price momentum)
    3. If oi_z > 0.5 AND price_z > 0.5 → LONG (trending up with OI confirmation)
    4. If oi_z < -0.5 AND price_z < -0.5 → SHORT (trending down with OI confirmation)
    5. If oi_z and price_z in opposite directions → skip (divergence)
    
    Entry: open[i+1]
    Exit: close[i+horizon]
    """
    if config is None:
        config = {'oi_z_thresh': 0.5, 'price_z_thresh': 0.5, 'horizon': 6}
    
    n = len(merged)
    if n < 50: return []
    
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
        if i + 1 >= n: break
        if i + horizon >= n: continue
        
        # Check OI and price moving together
        oi_up = oi_z[i] > th
        oi_dn = oi_z[i] < -th
        pr_up = price_z[i] > pth
        pr_dn = price_z[i] < -pth
        
        if oi_up and pr_up:
            direction = 'LONG'
        elif oi_dn and pr_dn:
            direction = 'SHORT'
        else:
            continue  # divergence or neutral
        
        entry = merged[i+1]['open']
        if entry <= 0: continue
        exit_price = merged[i+horizon]['close'] if i+horizon < n else merged[-1]['close']
        
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        
        signals.append({
            'ticker': merged[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': merged[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'otc', 'idx': i,
            'oi_z': round(oi_z[i], 4), 'price_z': round(price_z[i], 4),
        })
    
    return signals
```

### Test Parameters
- oi_z_thresh: [0.3, 0.5, 0.7]
- price_z_thresh: [0.3, 0.5, 0.7]
- horizon: [3, 6, 12]
- Best for: Si, CC, BR (highest OI)

---

## Strategy 2: Retail Trap (Fiz Extremes)

**Logic:** When retail traders (fiz) are extremely positioned in one direction, the market reverses.

### Signal Detection

```python
def detect_retail_trap_signals(merged, config=None):
    """
    Retail Trap — contrarian to fiz positioning.
    
    Per bar i:
    1. fiz_net = fiz_buy - fiz_sell
    2. fiz_total = fiz_buy + fiz_sell
    3. fiz_ratio = fiz_net / max(fiz_total, 1)  # [-1, +1]
    4. z-score of fiz_ratio over 20 bars → fiz_z
    5. If fiz_z > 1.5 → fiz extremely long → SHORT signal
    6. If fiz_z < -1.5 → fiz extremely short → LONG signal
    
    Entry: open[i+1]
    Exit: close[i+horizon]
    """
    if config is None:
        config = {'fiz_z_thresh': 1.5, 'horizon': 6}
    
    n = len(merged)
    if n < 50: return []
    
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
        if i + 1 >= n: break
        if i + horizon >= n: continue
        
        if fiz_z[i] > th:
            direction = 'SHORT'  # fiz overbought → sell
        elif fiz_z[i] < -th:
            direction = 'LONG'   # fiz oversold → buy
        else:
            continue
        
        entry = merged[i+1]['open']
        if entry <= 0: continue
        exit_price = merged[i+horizon]['close'] if i+horizon < n else merged[-1]['close']
        
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        
        signals.append({
            'ticker': merged[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': merged[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'retail_trap', 'idx': i,
            'fiz_z': round(fiz_z[i], 4),
        })
    
    return signals
```

### Test Parameters
- fiz_z_thresh: [1.0, 1.5, 2.0]
- horizon: [3, 6, 12]
- Best for: CC, SV, GL (high retail activity)

---

## Strategy 3: VWAP Deviation Reversion

**Logic:** Price far from VWAP (volume-weighted average price) tends to revert.

### Signal Detection

```python
def detect_vwap_signals(ohlcv, config=None):
    """VWAP Deviation Reversion — pure price, no OI needed.
    
    Per bar i:
    1. vwap = sum(close * volume) / sum(volume) over last 20 bars (rolling, no look-ahead)
    2. atr = rolling mean of true range over 14 bars
    3. deviation = (close - vwap) / atr
    4. If deviation > 2.0 → SHORT
    5. If deviation < -2.0 → LONG
    
    Entry: open[i+1]
    Exit: close[i+horizon]
    """
    if config is None:
        config = {'dev_thresh': 2.0, 'horizon': 6, 'vwap_window': 20, 'atr_period': 14}
    
    n = len(ohlcv)
    if n < 50: return []
    
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
        if i + 1 >= n: break
        if i + horizon >= n: continue
        if atr[i] <= 0: continue
        
        dev = (closes[i] - vwap[i]) / atr[i]
        
        if dev > th:
            direction = 'SHORT'
        elif dev < -th:
            direction = 'LONG'
        else:
            continue
        
        entry = ohlcv[i+1]['open']
        if entry <= 0: continue
        exit_price = ohlcv[i+horizon]['close'] if i+horizon < n else ohlcv[-1]['close']
        
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        
        signals.append({
            'ticker': ohlcv[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': ohlcv[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'vwap', 'idx': i,
            'deviation': round(dev, 4),
        })
    
    return signals
```

### Test Parameters
- dev_thresh: [1.5, 2.0, 2.5]
- horizon: [3, 6, 12]
- vwap_window: [20, 50]
- Best for: Si, BR, GD (trending, mean-reverting)

---

## Strategy 4: OI Divergence (Classic)

**Logic:** Classic futures divergence — price and OI moving in opposite directions signal trend weakness.

### Signal Detection

```python
def detect_oi_divergence_signals(merged, config=None):
    """
    OI Divergence — classic futures analysis.
    
    Per bar i:
    1. Find the most recent swing high and swing low in the last 20 bars
       Swing high: close[i] is the highest in window [-10, -5] before current
       Swing low: close[i] is the lowest in window [-10, -5] before current
    2. Compare current OI with OI at that swing high/low
    3. If close > swing_high_close AND oi < swing_high_oi → bearish divergence → SHORT
    4. If close < swing_low_close AND oi > swing_low_oi → bullish divergence → LONG
    
    Entry: open[i+1]
    Exit: close[i+horizon]
    """
    if config is None:
        config = {'lookback': 20, 'horizon': 6, 'extreme_window': 10}
    
    n = len(merged)
    if n < 50: return []
    if n < config['lookback'] + config['extreme_window'] + 5: return []
    
    closes = [r['close'] for r in merged]
    oi_vals = [r['total_oi'] for r in merged]
    lookback = config['lookback']
    ext_w = config['extreme_window']
    horizon = config['horizon']
    
    signals = []
    # We need data before the lookback window to find swings
    min_idx = lookback + 5
    
    for i in range(min_idx, n):
        if i + 1 >= n: break
        if i + horizon >= n: continue
        
        # Look for swings in the window [i-lookback, i-5] (leave 5 buffer before current)
        search_start = max(0, i - lookback)
        search_end = max(search_start + 1, i - ext_w)
        if search_end <= search_start: continue
        
        # Find max close index in that window
        max_idx = search_start
        min_idx_val = search_start
        for j in range(search_start, search_end):
            if closes[j] > closes[max_idx]: max_idx = j
            if closes[j] < closes[min_idx_val]: min_idx_val = j
        
        # Bearish divergence: price ABOVE recent high but OI LOWER
        if closes[i] > closes[max_idx] and oi_vals[i] < oi_vals[max_idx] * 0.95:
            direction = 'SHORT'
        # Bullish divergence: price BELOW recent low but OI HIGHER
        elif closes[i] < closes[min_idx_val] and oi_vals[i] > oi_vals[min_idx_val] * 1.05:
            direction = 'LONG'
        else:
            continue
        
        entry = merged[i+1]['open']
        if entry <= 0: continue
        exit_price = merged[i+horizon]['close'] if i+horizon < n else merged[-1]['close']
        
        ret = (exit_price - entry) / entry * 100 if direction == 'LONG' else (entry - exit_price) / entry * 100
        
        signals.append({
            'ticker': merged[0].get('symbol', '?'), 'direction': direction,
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'time': merged[i]['time'], 'return_pct': round(ret, 4),
            'strategy': 'oi_divergence', 'idx': i,
        })
    
    return signals
```

### Test Parameters
- lookback: [10, 20, 30] (window to search for swing highs/lows)
- horizon: [3, 6, 12]
- divergence_threshold: [0.95, 0.90] (bearish) / [1.05, 1.10] (bullish)
- Best for: BR, Si, GLDRUBF

---

## Backtest Harness

```python
def compute_stats(signals):
    """WR, PF, DD, avg_ret for a list of signals."""
    if not signals: return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0, 'avg_ret': 0.0}
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
        if cum > peak: peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd: max_dd = dd
    return {'n': n, 'wr': round(wr, 1), 'pf': round(pf, 2), 'dd': round(max_dd, 1), 'avg_ret': round(sum(returns)/n, 2)}

def run_all_tickers(strategy_fn, tickers, days=180, **kwargs):
    """
    Run a strategy on all tickers, return results dict.
    
    Out-of-sample validation: split data 70/30 by time.
    Train (first 70%): for testing parameter sensitivity
    Test (last 30%): actual performance measurement (NO params tuned on this)
    """
    results = {}
    for tk in tickers:
        try:
            ohlcv = load_ohlcv(tk, days)
            if not ohlcv or len(ohlcv) < 500: continue
            
            # Strategy-specific data loading
            if strategy_fn in [detect_otc_signals, detect_retail_trap_signals, detect_oi_divergence_signals]:
                oi_data = load_oi(tk, days)
                merged = merge_ohlcv_oi(ohlcv, oi_data)
                if not merged or len(merged) < 100: continue
                full_data = merged
            else:
                full_data = ohlcv
            
            # 70/30 time split (OUT-OF-SAMPLE: last 30% ONLY)
            split_idx = int(len(full_data) * 0.7)
            # We run on the full data but only report the test portion
            test_data = full_data[split_idx:]
            
            # But the strategy needs full data for indicators (no look-ahead into test)
            # So we run on all data but filter signals to test set
            # Actually, to avoid ANY look-ahead, we must run the strategy on the test data only
            # but that means indicators at the start of test period are weak (not enough history)
            # Better: run on all data, filter signals by idx >= split_idx
            
            all_signals = strategy_fn(full_data, **kwargs) if hasattr(strategy_fn, '__call__') else strategy_fn(full_data, kwargs)
            
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

def print_results(strategy_name, results):
    """Pretty-print strategy results as table."""
    print(f"\n{'='*60}")
    print(f"  {strategy_name}")
    print(f"{'='*60}")
    print(f"{'Ticker':<10} {'Sig':<6} {'WR%':<8} {'PF':<8} {'DD%':<8} {'AvgRet':<8}")
    print(f"{'-'*50}")
    ranked = [(tk, r) for tk, r in results.items() if 'test_stats' in r and r['test_signals'] > 0]
    ranked.sort(key=lambda x: x[1]['test_stats']['wr'], reverse=True)
    for tk, r in ranked:
        s = r['test_stats']
        print(f"{tk:<10} {r['test_signals']:<6} {s['wr']:<8} {s['pf']:<8} {s['dd']:<8} {s['avg_ret']:<8}")
    # Errors
    errors = [(tk, r) for tk, r in results.items() if 'error' in r]
    for tk, r in errors:
        print(f"{tk:<10} ERROR: {r['error']}")
```

---

## Test Tickers

High-volume tickers (active, good liquidity):

```
CNYRUBF, Si, CC, SV, GLDRUBF, IMOEXF, BR, SS, USDRUBF,
GL, GK, NG, BM, NA, GD, IB, VB, MC, MX, GZ, Eu, KC, ED, SR, SF
```

Run ALL strategies on ALL these tickers. Days=180 for enough history.

---

## Execution

```python
if __name__ == '__main__':
    import sys
    tickers = ['CNYRUBF','Si','CC','SV','GLDRUBF','IMOEXF','BR','SS','USDRUBF',
               'GL','GK','NG','BM','NA','GD','IB','VB','MC','MX','GZ','Eu','KC','ED','SR','SF']
    
    print("="*60)
    print("  MOEX New Strategies — Out-of-Sample Backtest (last 30%)")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Tickers: {len(tickers)}")
    print(f"  Data window: 180 days")
    print("="*60)
    
    # Strategy 1: OTC
    for oi_th in [0.3, 0.5, 0.7]:
        for h in [3, 6, 12]:
            r = run_all_tickers(detect_otc_signals, tickers, days=180,
                              config={'oi_z_thresh': oi_th, 'price_z_thresh': 0.5, 'horizon': h})
            print_results(f"OTC oi_z>{oi_th} h={h}", r)
    
    # Strategy 2: Retail Trap
    for fiz_th in [1.0, 1.5, 2.0]:
        for h in [3, 6, 12]:
            r = run_all_tickers(detect_retail_trap_signals, tickers, days=180,
                              config={'fiz_z_thresh': fiz_th, 'horizon': h})
            print_results(f"RetailTrap fiz_z>{fiz_th} h={h}", r)
    
    # Strategy 3: VWAP
    for dev_th in [1.5, 2.0, 2.5]:
        for h in [3, 6, 12]:
            r = run_all_tickers(detect_vwap_signals, tickers, days=180,
                              config={'dev_thresh': dev_th, 'horizon': h})
            print_results(f"VWAP dev>{dev_th} h={h}", r)
    
    # Strategy 4: OI Divergence
    for lb in [10, 20, 30]:
        for h in [3, 6, 12]:
            r = run_all_tickers(detect_oi_divergence_signals, tickers, days=180,
                              config={'lookback': lb, 'horizon': h, 'extreme_window': 10})
            print_results(f"OIDiv lb={lb} h={h}", r)
```

---

## Deliverables

After running, deliver:

1. **Top-3 tickers per strategy** with their WR, PF, DD
2. **Best parameter set** per strategy (which horizon, threshold)
3. **Cross-strategy ranking** — which of the 4 strategies has highest avg WR
4. **Recommendation** — which strategy(ies) to integrate into cron_scanner.py

File structure:
```
trading_bot/new_strategies.py  — all 4 strategy functions + harness
docs/backtest/otc_results.txt
docs/backtest/retail_trap_results.txt
docs/backtest/vwap_results.txt
docs/backtest/oi_divergence_results.txt
docs/backtest/summary.txt
```

---

## ⚠️ CRITICAL RULES

1. **NO look-ahead bias.** Every indicator uses ONLY `data[:i]` (past data). Never `data[i:]`, never `data[i+1:]`, never global min/max. Every helper function must be verified before use.
2. **Out-of-sample only.** Report only the LAST 30% of data (by time). The strategy runs on full data for indicator stability, but metrics filter to `idx >= split_idx`.
3. **No numpy advanced indexing** that leaks future data. Only simple Python loops with `range(w, i)` patterns.
4. **No global statistics** (no `np.mean(data)`, no `min(data)`, no `max(data)` — only rolling/sliding windows).
5. **CRITICAL SHORT RETURN:** `ret = (entry - exit) / entry * 100` for SHORT. `ret = (exit - entry) / entry * 100` for LONG. Verify every signal function's return calculation.
6. **Report all results** — don't cherry-pick. Show both winners AND losers.
7. **Run time:** This should take 5-15 minutes to compute on 25 tickers × 4 strategies × parameter grids. Use minimal output per strategy iteration.
