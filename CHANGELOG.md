# Changelog

## [114] 2026-06-28
### Added
- Portfolio test complete — 7 tickers, 3 strategies, 18 months
- RiskManager (DD-stop 20%, max 5 concurrent)
- Commission fix (4 RUB × 2, entry+exit)

### Changed
- sizing: pure risk formula int(equity × 10% / GO), no caps
- contracts=NULL in portfolio (no artificial limits)
- AGENTS.md updated with final metrics

### Removed
- Churn disabled (WR 58.6% but negative PnL)

## [113] 2026-06-28
### Added
- RiskManager, commission fix, contracts cap
- risk.py, Backtester, PaperTrader
- Engine: all 4 strategies firing, O(n²)→O(n) price_10

## [112] 2026-06-28
- Architecture complete: Backtester + PaperTrader, portfolio in PG
