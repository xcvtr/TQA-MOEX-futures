# Changelog

## [119] 2026-06-28
### Fixed
- **CRITICAL look-ahead**: сигнал на bar[i], вход на open[i+1] + 1 tick.
  Stop Hunt соло: было 100K→95.9B, стало 100K→1.13M (+1,029%).
### Results
- Финальный реалистичный портфель: 100K→201K (+101%), 107 сделок, WR 64.5%, MDD 23.4%.

## [118] 2026-06-28
- TRIZ fix: immediate stop execution, zero entry slippage

## [117] 2026-06-28
- Audit complete, TRIZ analysis via OpenCode

## [116-112] 2026-06-28
- Architecture: Backtester, PaperTrader, RiskManager, portfolio in PG
