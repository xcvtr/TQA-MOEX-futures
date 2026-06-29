# Changelog

## [126] 2026-06-29
### Pre-production live
- PaperTrader: catch_up(), use_pg, trailing state save/restore
- Loader: snapshot API вместо candles, prefix mapping
- PG prices: backfill 700 bars from CH, live update every 15 min
- Dashboard: systemd service, weight, RM status
- Portfolio weight: GZ=1.5, Si=1.2, etc.
- Lunch Reversal: disabled

## [125] 2026-06-28
- PaperTrader first trade: Lunch Reversal +6,219 RUB

## [124-120] 2026-06-28
- Loader, PG prices, prod/test separation
- Backtester, PaperTrader, RiskManager

## [119-112] 2026-06-28
- Architecture: Engine→Executor→Broker, audit, TRIZ fixes
- Realistic portfolio: 100K→201K (+101%), 64.5% WR
