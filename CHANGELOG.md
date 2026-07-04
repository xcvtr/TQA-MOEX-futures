# Changelog

## [136] 2026-07-04
### Fixed
- CH cluster recovery: all stuck tables → ReplicatedReplacingMergeTree, 2 replicas
- Obstats backfill: 85.5M rows (more than original 46.9M) from AlgoPack API
### Changed
- Stop Hunt backtest — corrected from 81.8% WR (with look-ahead) to 51.5% (honest)
- Timeout calibration: TO=12 bars confirmed optimal (1.65 PF at 50.7% WR)
### Added
- Checkpoint: checkpoint/136-ch-cluster-recovery-backtest.md
