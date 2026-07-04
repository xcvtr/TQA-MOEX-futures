## [140] 2026-07-04
### Changed
- Final portfolio composition: GZ, Si, RN, GD (NG, W4, VB, SR disabled)
- Stop Hunt scan completed: 60 tickers, top by Sharpe (RN 33.8, GD 24.7)
- Answer: strategy is Stop Hunt — false breakout from MQL5 Excavator port
### Fixed
- bugs documented in paper_trader.py (entry lag, CVD dead, timeout, lot check)
### Added
- Checkpoint: checkpoint/140-stop-hunt-strategy-session.md
# Changelog

## [137] 2026-07-04
### Changed
- VB and SR removed from portfolio (negative PnL, -35K total)
- Stop Hunt partial exit tested — kills strategy (PnL goes negative)
- CVD filter tested — improves PF but cuts trade count by 43%
### Fixed
- Portfolio: SR and VB disabled in PG futures.portfolio
### Added
- Checkpoint: checkpoint/137-strategy-improvements.md

## [136] 2026-07-04
### Fixed
- CH cluster recovery: all stuck tables → ReplicatedReplacingMergeTree, 2 replicas
- Obstats backfill: 85.5M rows (more than original 46.9M) from AlgoPack API
### Changed
- Stop Hunt backtest — corrected from 81.8% WR (with look-ahead) to 51.5% (honest)
- Timeout calibration: TO=12 bars confirmed optimal (1.65 PF at 50.7% WR)
### Added
- Checkpoint: checkpoint/136-ch-cluster-recovery-backtest.md
