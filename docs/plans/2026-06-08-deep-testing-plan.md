# Comprehensive Strategy Testing Plan

**Goal:** Deep-test all 7 MOEX strategies — fill gaps in validation.

**Project:** TQA-MOEX, ~/projects/TQA-MOEX/
**DB:** host=10.0.0.64, db=moex, user=postgres, password=***
**Data:** moex_prices_5m (OHLCV) + moex_prices_5m_oi (fiz_buy/fiz_sell/yur_buy/yur_sell/total_oi)

**NO look-ahead in ANY test.** All indicators use only data[:i].

---

## Task 1: Walk-Forward Validation (All 7 Strategies)

Replace the single 70/30 split with rolling walk-forward.

**How:**
- Data: 365 days of 5m data per ticker
- 4 folds: split into 4 sequential periods
- Each fold: train on earlier 75%, test on later 25%
- Report: WR, PF, DD per fold + average + std

**Which strategies to test:**

| Strategy | Module | Function | Tickers |
|:---------|:-------|:---------|:--------|
| VS | trading_bot.engine | detect_signals | HS, KC, DX, HY, BM |
| Reversion | trading_bot.reversion_engine | detect_mean_reversion_signals | NM, AF |
| OB | trading_bot.ob_engine | detect_order_block_signals | SBERF, BR |
| VWAP | trading_bot.vwap_engine | detect_vwap_signals | GZ, Eu, SR, Si, MC |
| OTC | trading_bot.new_strategies | detect_otc_signals | CNYRUBF, Si, CC, SV |
| Retail Trap | trading_bot.new_strategies | detect_retail_trap_signals | CNYRUBF, GZ, BR |
| OI Divergence | trading_bot.new_strategies | detect_oi_divergence_signals | SV, BR, BM |

**Output format:**
```
=== Walk-Forward: VWAP on GZ ===
Fold 1 (Jan-Mar): WR=56.2% PF=1.18 n=312
Fold 2 (Apr-Jun): WR=58.7% PF=1.22 n=298
Fold 3 (Jul-Sep): WR=54.1% PF=1.09 n=335
Fold 4 (Oct-Dec): WR=57.3% PF=1.15 n=301
Average: WR=56.6%±2.0% PF=1.16±0.05
```

**⚠️ CRITICAL:** Each fold uses ONLY data available at fold time. Fold 2 trains on fold 1 period, tests on fold 2 period. Never peek forward.

Also note: the 3 new strategies (OTC, Retail Trap, OI Divergence) need OI data. Load both price and OI, merge on time. Functions accept `merged` list of dicts with ALL keys: time, open, high, low, close, volume, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi.

**File output:** `docs/backtest/walkforward_summary.txt`

---

## Task 2: SHORT Return Cross-Check (All Strategies)

This is the most common bug. Verify that SHORT signals correctly compute `(entry - exit) / entry`.

**Method:** For each strategy, pick the top ticker, get 100 signals, independently recompute return.

```python
def crosscheck_returns(strategy_name, detect_fn, ticker, data, config):
    signals = detect_fn(ticker, data, config)
    errors = 0
    for s in signals[:100]:
        entry = s['entry']
        exit_price = s['exit']
        dir = s['direction']
        expected_ret = ((exit_price - entry) / entry * 100) if dir == 'LONG' else ((entry - exit_price) / entry * 100)
        if abs(round(expected_ret, 4) - s['return_pct']) > 0.001:
            errors += 1
            print(f"  ❌ {dir} entry={entry} exit={exit_price} got={s['return_pct']} expected={expected_ret:.4f}")
    print(f"{strategy_name} on {ticker}: {errors} errors / {min(100, len(signals))} signals")
```

Expected: **0 errors** for ALL 7 strategies.

**File output:** `docs/backtest/return_crosscheck.txt`

---

## Task 3: Parameter Sensitivity (VWAP + OB)

Test: if you shift parameters by ±10%, does WR change dramatically?

**Method:** For each strategy on its best ticker:
1. Run best params → get WR_base
2. Run with each param ±10%, ±20%
3. Count how many parameter shifts keep WR > WR_base - 5%

**VWAP on GZ:**
- Base: dev_thresh=2.0, horizon=12, vwap_window=20
- Test: dev_thresh ∈ [1.6, 1.8, 2.0, 2.2, 2.4]
- Test: horizon ∈ [6, 9, 12, 15, 18]
- Test: vwap_window ∈ [10, 15, 20, 30, 50]

**OB on SBERF:**
- Base: body_mul=1.5, horizon=4
- Test: body_mul ∈ [1.2, 1.35, 1.5, 1.65, 1.8]
- Test: horizon ∈ [2, 3, 4, 6, 8]

**Criterion:** If WR drops >5% when a param shifts ±10% → strategy is FRAGILE.

**File output:** `docs/backtest/sensitivity_vwap.txt`, `docs/backtest/sensitivity_ob.txt`

---

## Task 4: Signal Overlap Analysis

Check: do different strategies signal on the SAME bars? If they do, opening both is redundant.

**Method:** For each pair of strategies that share tickers:
- Get signals from both on the same data period
- Count signals where |time_sigA - time_sigB| < 5 minutes
- overlap_ratio = overlapping / min(nA, nB)

**Pairs to check:**
- Reversion vs VWAP on NM (Reversion was split to NM+AF, but let's check)
- OB vs VWAP — no shared tickers (OB has SBERF+BR, VWAP has GZ+Eu+SR+Si+MC)
- VS vs any — VS tickers are unique (HS, KC, DX, HY, BM)

Currently there should be **NO overlap** since tickers were split. This test confirms.

**File output:** `docs/backtest/signal_overlap.txt`

---

## Task 5: Market Regime Analysis (VWAP + OB)

Do strategies work in ALL market conditions, or only in trending/sideways?

**Method:**
1. Load 365 days of data for top ticker
2. Calculate ADX (14) for each bar → classify:
   - ADX > 25: TRENDING
   - ADX < 15: SIDEWAYS
   - 15-25: MIXED
3. For each regime, compute WR/PF of signals that fire IN that regime
4. Also by VIX proxy: use ATR(14)/close as volatility regime:
   - ATR/close > 0.01: HIGH VOL
   - ATR/close < 0.005: LOW VOL

**Tickers:** VWAP on GZ, OB on SBERF

**File output:** `docs/backtest/regime_analysis.txt`

---

## Task 6: Simple Monte Carlo (VWAP)

Check: is the WR statistically significant, or could it be random?

**Method:**
1. Take the actual signals from VWAP on GZ (test period only)
2. SHUFFLE the return_pct values (preserve count, randomize order)
3. Recompute WR on shuffled data
4. Repeat 1000 times
5. Count how many shuffled runs have WR >= actual WR
6. If < 5% (p < 0.05), result is statistically significant

```python
import random
actual_wr = 58.7  # from backtest
signals = [...]  # actual signals, extract return_pct
returns = [s['return_pct'] for s in signals]

count = 0
N = 1000
for _ in range(N):
    random.shuffle(returns)
    wr = sum(1 for r in returns if r > 0) / len(returns) * 100
    if wr >= actual_wr:
        count += 1

p_value = count / N
print(f"p-value: {p_value:.4f} (significant if < 0.05)")
```

**File output:** `docs/backtest/monte_carlo_vwap.txt`

---

## Task 7: Slippage/Commission Modeling (VWAP + OB)

How much does 1 tick of slippage cost?

**Method:** For top ticker of each strategy:
- Run backtest normally → WR_base, PF_base
- Apply 1 tick slippage on entry: entry' = entry + tick (for LONG) or entry - tick (for SHORT)
- Apply 1 tick slippage on exit: exit' = exit - tick (for LONG) or exit + tick (for SHORT)
- Recompute WR, PF with slippage
- Report difference

For VWAP on GZ: tick_rub = 0.01 (from vwap_engine config)
For OB on SBERF: tick_rub = 1.0

**File output:** `docs/backtest/slippage_analysis.txt`

---

## Execution

```python
import sys, os, json, random
from datetime import datetime, timedelta, timezone
import psycopg2

DB = dict(host='10.0.0.64', port=5432, dbname='moex', user='postgres', password='postgres')

# Helper: load OHLCV as dicts
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
        if oi_row: merged.append({**r, **oi_row})
    return merged

# Import strategy functions
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

# Strategy registry for testing
STRATEGIES = {
    'vs': {'fn': vs_detect, 'tickers': ['HS','KC','DX','HY','BM'], 'config': DEFAULT_CONFIG, 'needs_oi': True, 'loader': 'row'},  # load_bars_with_oi
    'reversion': {'fn': rev_detect, 'tickers': ['NM','AF'], 'config': DEFAULT_REVERSION_CONFIG, 'needs_oi': False, 'loader': 'ohlcv'},
    'ob': {'fn': ob_detect, 'tickers': ['SBERF','BR'], 'config': DEFAULT_OB_CONFIG, 'needs_oi': False, 'loader': 'ohlcv'},
    'vwap': {'fn': vwap_detect, 'tickers': ['GZ','Eu','SR','Si','MC'], 'config': DEFAULT_VWAP_CONFIG, 'needs_oi': False, 'loader': 'ohlcv'},
    'otc': {'fn': detect_otc_signals, 'tickers': ['CNYRUBF','Si','CC','SV'], 'config': {'oi_z_thresh': 0.3, 'price_z_thresh': 0.5, 'horizon': 12}, 'needs_oi': True, 'loader': 'merged'},
    'retail_trap': {'fn': detect_retail_trap_signals, 'tickers': ['CNYRUBF','GZ','BR'], 'config': {'fiz_z_thresh': 1.5, 'horizon': 12}, 'needs_oi': True, 'loader': 'merged'},
    'oi_divergence': {'fn': detect_oi_divergence_signals, 'tickers': ['SV','BR','BM'], 'config': {'lookback': 20, 'horizon': 3, 'extreme_window': 10, 'bear_threshold': 0.9, 'bull_threshold': 1.1}, 'needs_oi': True, 'loader': 'merged'},
}

# Load data for a strategy on a ticker
def load_for_strategy(strategy_name, ticker, days=365):
    info = STRATEGIES[strategy_name]
    ohlcv = load_ohlcv(ticker, days)
    if not ohlcv: return []
    if info['loader'] == 'merged':
        oi = load_oi(ticker, days)
        return merge_ohlcv_oi(ohlcv, oi)
    elif info['loader'] == 'row':
        # VS expects tuples from load_bars_with_oi
        oi = load_oi(ticker, days)
        merged = merge_ohlcv_oi(ohlcv, oi)
        # Convert to tuple format
        return [(r['time'], r.get('fiz_buy',0), r.get('fiz_sell',0), 
                 r.get('yur_buy',0), r.get('yur_sell',0), r['close'], r['volume'], r['open']) 
                for r in merged]
    else:
        return ohlcv  # dict format for vwap/reversion/ob (wrappers convert to tuples)

def compute_stats(signals):
    if not signals: return {'n':0,'wr':0.0,'pf':0.0,'dd':0.0,'avg_ret':0.0}
    returns = [s['return_pct'] for s in signals]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = len(wins)/n*100
    sum_wins = sum(wins) if wins else 0
    sum_losses = abs(sum(losses)) if losses else 0
    pf = sum_wins/sum_losses if sum_losses > 0 else (999.99 if sum_wins > 0 else 0)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in returns:
        cum += r
        if cum > peak: peak = cum
        dd = peak - cum
        if peak > 0 and dd > max_dd: max_dd = dd
    return {'n': n, 'wr': round(wr,1), 'pf': round(pf,2), 'dd': round(max_dd,1), 'avg_ret': round(sum(returns)/n,4) if n else 0}

def print_results(title, results_dict):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    for tk, stats in sorted(results_dict.items(), key=lambda x: x[1]['wr'], reverse=True):
        print(f"  {tk:<10} n={stats['n']:<6} WR={stats['wr']:<6}% PF={stats['pf']:<8} DD={stats['dd']:<6}%")
```

## Run all 7 tasks:

```python
# Task 1: Walk-Forward
print("\n\n═══ TASK 1: WALK-FORWARD ═══")
# ... 4-fold walk-forward for each strategy

# Task 2: Cross-Check
print("\n\n═══ TASK 2: RETURN CROSS-CHECK ═══")
# ...

# Task 3: Sensitivity
print("\n\n═══ TASK 3: PARAMETER SENSITIVITY ═══")
# ...

# etc.
```

**Save ALL results to:** `docs/backtest/deep_test_<task>.txt`

## ⚠️ CRITICAL RULES

1. **NO look-ahead.** Every indicator uses only data[:i]. Verified by design.
2. **SHORT return:** `(entry - exit) / entry * 100`. ALWAYS verify.
3. **LONG return:** `(exit - entry) / entry * 100`.
4. **report ALL results** — winners AND losers.
5. **Don't cherry-pick.** Show the full picture.
6. **Run time:** Task 1 is the heaviest (4 folds × 7 strategies × ~5 tickers each = ~140 runs). Each run takes ~20s. Total ~45 minutes. Run sequentially with progress output.
7. **Tasks 2-7** are fast (<5 min each).

---

## Deliverables

```
docs/backtest/deep_test_1_walkforward.txt
docs/backtest/deep_test_2_return_crosscheck.txt
docs/backtest/deep_test_3_sensitivity.txt
docs/backtest/deep_test_4_overlap.txt
docs/backtest/deep_test_5_regime.txt
docs/backtest/deep_test_6_monte_carlo.txt
docs/backtest/deep_test_7_slippage.txt
```

And a final `docs/backtest/deep_test_summary.txt` with:
- Which strategies pass all tests
- Which are fragile (high sensitivity)
- Which have significant WR (low p-value)
- Which overlap with others
- Recommendation on what to trade
