# Changelog

## [118] 2026-06-28
### Fixed
- **TRIZ: Immediate stop execution** — трейлинг-стоп исполняется на том же баре,
  а не на следующем. Эффективный трейлинг 0.3% вместо 0.8-1.3%.
- **Entry slippage 0** — сигнал на close = вход по close, без лишнего тика.
- **Результат**: портфель из убыточного (-20.8%) стал прибыльным (+2,669%).

## [117] 2026-06-28
- Audit complete: trailing fix, market stops, slippage, liquidity, commission
- .omo/ added to gitignore

## [116] 2026-06-28
- CRITICAL BUG: multi-ticker position management fix
- Realistic results after audit

## [115] 2026-06-28
- Audit: trailing formula, market stops, slippage, liquidity, commission

## [114] 2026-06-28
- Portfolio test complete, risk-based sizing

## [113] 2026-06-28
- RiskManager, commission fix, contracts cap
- Engine: all 4 strategies firing
