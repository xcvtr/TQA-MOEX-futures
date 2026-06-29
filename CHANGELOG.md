# Changelog

## [132] 2026-06-30
### Added
- AlgoPack bars loader (algopack_bars.py) — 5-min bars with vol_b/vol_s/oi
- Cron: algopack bars every 5 min during trading hours
- FUTOI via moexalgo (same loader)
- CH moex.bars (ReplicatedReplacingMergeTree) + moex.futoi

### Fixed
- vol_b/vol_s backfill into PG from CH (43K bars updated)
- PaperTrader catch_up uses Engine (proper indicator computation)
- RISK_PCT 0.1 → 0.02 (realistic)
- Endpoint: apim.moex.com (not iss.moex.com)

## [131] 2026-06-29
- PaperTrader catch_up, tick() only mode
- Architecture: Engine→Executor→Broker→PaperTrader

## [130-101] 2026-06-28/29
- Complete rewrite: audit, TRIZ fixes, look-ahead fix
- Realistic backtester, PG/CH separation
