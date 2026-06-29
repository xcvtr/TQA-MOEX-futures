# Changelog

## [128] 2026-06-29
### Final architecture
- PRI=PG (PaperTrader, prices 2mo, portfolio, state)
- STDBY=CH (Backtester, tradestats_fo 18+mo)
- PG: 43,779 bars across 7 tickers, 28 Apr → 19 Jun
- Autopurge: DELETE > 2 months on every write
- PaperTrader PG-only (use_pg=True, self.ch=None)

## [127] 2026-06-29
- Pre-prod: cron, dashboard, trailing state, weights

## [126-112] 2026-06-28
- Architecture, audit, TRIZ fixes, realistic backtester
