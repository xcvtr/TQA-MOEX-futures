# Changelog

## [134] 2026-06-30
### Fixed
- AlgoPack bars: filter daily snapshots, keep only real 5-min bars (minute % 5 == 0)
- 12,360 proper bars loaded for June 30
- PaperTrader state reset to 100K

## [133] 2026-06-30
- AlgoPack API working, bars with vol_b/vol_s/oi
- PaperTrader catch_up via Engine
- RISK_PCT 0.02

## [132] 2026-06-30
- Architecture: PRI=PG, STDBY=CH
- All crons setup

## [131-101] 2026-06-28/29
- Complete rewrite: audit, TRIZ, backtester, PaperTrader
