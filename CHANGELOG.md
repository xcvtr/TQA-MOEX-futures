## [141] 2026-07-04
### Added
- Stop Hunt COMBINED (SHORT+LONG) backtest: 5 tickers, 56.4% WR, 2.03 PF, +7.3M
### Changed
- Portfolio: GD (GOLD) and RN (ROSN) added — GD 59.3% WR best performer
- LONG direction (60.5% WR) > SHORT (48.3%) — both kept for paper trader
- CR (CNYRUBF) confirmed: no data in tradestats_fo
### Fixed
- bt_5t.py: lot_volume added to spec query (was KeyError)
- bt_5t.py: CORRECT — PnL formula WITHOUT lot (step_price per contract only)
- Paper trader bugs documented (entry lag, CVD dead, timeout broken)

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
