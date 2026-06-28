# Changelog

## [112] 2026-06-28
### Added
- Backtester — загрузка данных из CH + Engine + метрики
- PaperTrader — циклический раннер, состояние в PG
- futures.portfolio (17 rows, 7 tickers × 4 strategies)
- futures.paper_state

### Changed
- Полная архитектура: Broker → Executor → Engine → Backtester/PaperTrader
- config.py — очищен от мусора (TICKER_MAP, BASE_V2_*, CORRELATION_GROUPS)
- AGENTS.md — актуализирован

### Fixed
- Executor: overlapping positions (skip if open for ticker)
- Executor: safeguard inf/nan equity
- Broker: safeguard min_step=0

### Removed
- scripts/, configs/, data/, docs/, trading_bot/ → archive
- public.moex_ticker_specs, futures.strategy_cvd_portfolio (PG)
- 107 legacy root scripts → archive
- trailing_tp.py (logic merged into BrokerSim)
