# Volume × OI Backtest: NR + CC + IB

**Strategy:** Yur Accumulation (vol_z>threshold AND yur_z>threshold AND fiz_z<0)  
**Capital:** 200,000 ₽  
**Commission:** 2 ₽/contract (round-trip)  
**Bar-level MTM:** ✅ entry on signal bar close, MTM on every 5-min OHLCV bar, stop-loss, time-stop

## Parameters
| Ticker | Vol_z | Yur_z | Horizon | Stop-loss | Minstep | Tick RUB |
|--------|:-----:|:-----:|:-------:|:---------:|:-------:|:--------:|
|     NR | >3.0σ | >1.5σ | 6b | 2% | 0.01 | 1.0 |
|     CC | >3.5σ | >1.5σ | 3b | 1% | 0.01 | 1.0 |
|     IB | >3.5σ | >2.0σ | 12b | 2% | 0.01 | 1.0 |

## Results

| Metric | Value |
|--------|-------|
| Total trades | 3 |
| Win rate | 100.0% |
| Gross PnL |  +152830 ₽ |
| Commission |       30 ₽ |
| Net PnL |  +152800 ₽ |
| Net Return |  +76.40% |
| Max DD | 0.57% |
| Calmar | 134.78 |
| Final Equity |   352800 ₽ |

## Per-ticker
| Ticker | Trades | WR | PnL | Avg PnL |
|--------|-------:|:--:|----:|--------:|
|     CC |   1 | 100% |   +47350 ₽ |  +47350 ₽ |
|     IB |   1 | 100% |     +480 ₽ |    +480 ₽ |
|     NR |   1 | 100% |  +105000 ₽ | +105000 ₽ |
