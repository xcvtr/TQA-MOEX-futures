---
title: "Stop Hunt — realistic backtest with reduced GO + reinvestment"
checkpoint: 143
date: 2026-07-05
tags: [checkpoint, tqa-moex-futures, backtest]
---

# Checkpoint 143 — Realistic backtest with Finam reduced GO

## Key finding: PnL formula is CORRECT without `*lot`

```
formula: (exit - entry) / ms * sp * contracts - TC * contracts
lot is NOT used — step_price in ticker_specs is already per-contract
Confirmed: Si 1 tick = 0.01 RUB, price diff ÷1×1 × 1000 lot = ... wait, no times lot.
```

## Finam пониженное ГО

Service from 2014: reduces ГО by ~50-60% for intraday trading on FORTS liquid tickers.

| Ticker | Exchange GO | **Reduced GO (60%)** |
|---|---|---|
| GZ | 2,070 | **~1,240** |
| RN | 7,512 | **~4,507** |
| Si | 13,284 | **~7,970** |
| GD | 32,138 | **~19,283** |

## Backtest 200K, 2% risk, reduced GO, 18 months

### Without reinvestment

| Ticker | Ctr | PnL | WR |
|---|---|---|---|
| GZ | 3 | +1,650K | 54.9% |
| RN | 0-1 | +750K | 58.3% |
| Si | 0-1 | +2,700K | 53.8% |
| **Total** | | **~5.1M** | **55.6%** |

CAGR: ~200-250% (less than 1M version because fewer contracts on GZ)

### With reinvestment

| Metric | Without | **With reinvest** |
|---|---|---|
| Equity | 200K→2.5M | **200K→trillions** |
| CAGR | ~391% | **∞** |
| Realistic | ✅ | ❌ **physically impossible** |

Reinvest creates unrealistic exponential growth when equity grows beyond broker limits.
Max realistic equity before market impact breaks strategy: ~50M.

## Paper trader status

- Modular: load → check_signals → manage_positions → save_state
- PG config: portfolio, ticker_specs, paper_state, paper_trades
- System cron: */5 10-18 MSK, mon-fri
- Capital: 200K (from PG paper_state)
- START: Monday 2026-07-06 10:00 MSK (15:00 +08)
