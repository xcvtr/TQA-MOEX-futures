# Plan: Mean Reversion Walk-Forward + Trading Bot Integration

## Task 1: Walk-Forward Validation
Run walk-forward test for Mean Reversion After Volatility Exhaustion.

**Method:**
- 6 months total data (moex_prices_5m)
- Train: months 1-3 → find optimal params (mid, horizon)
- Test: months 4-6 → evaluate OOS with fixed params from train
- Do this for each of the top tickers: NM, NR, BR, SBERF, MM, AF

**Expected output per ticker:** train WR/PF/n vs test WR/PF/n with same params.

## Task 2: Analysis
If test WR ≥ train WR - 10pp → strategy is real.
If test WR drops more → overfitted.

## Task 3: Trading Bot Integration (conditional on task 2)

Only if OOS passes, add MeanReversionEngine to trading_bot:

```
trading_bot/
├── reversion_engine.py  # NEW — detect_mean_reversion_signals()
├── __init__.py          # PATCH — add REVERSION_TICKERS config
├── cron_scanner.py      # PATCH — include reversion signals
```

**Signal logic (same as scan_reversion_all3.py):**
1. Load 5m bars for ticker
2. For each bar i (i >= 25):
   - vol_z[i] = z-score of volume over last 20 bars
   - pos[i] = (close - low) / (high - low)
   - mr = rolling median of range over last 50 bars
   - Condition: vol_z[i] >= 1.5 AND range[i] >= mr * 1.5 AND pos[i] in [0.3, 0.7]
   - Check 3 prior bars: all close > open (3 up → SHORT signal) or all close < open (3 down → LONG signal)
3. Entry: close[i] (next bar: open[i+1])
4. Exit: after 12 bars (or 24)
5. Signal format: same as Volume Surge {ticker, direction, entry, time, vol_z, ...}

**Tickers:** NM, BR, SBERF, MM, AF, NR
**Horizon:** 12 bars (primary), 24 bars (secondary)
**Mid-range:** [0.3, 0.7], fallback [0.2, 0.8]
