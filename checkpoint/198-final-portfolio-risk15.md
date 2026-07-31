# Checkpoint 198: Финальный портфель ALLFUT — risk MDD 15%, +633% ROI

**Дата:** 2026-07-31
**Теги:** checkpoint, portfolio, final, allfut, risk

## Результаты (2.5 года данных ALLFUT, common pool, без look-ahead)

| Стратегия | Тикер | TF | Risk | WR | PF | PnL |
|:----------|:------|:--:|:----:|:--:|:--:|----:|
| IR | LKOH (Лукойл) | 5m | 45% | **64.9%** | 4.04 | +52K |
| IR | SNGP (Сургут-п) | 5m | 45% | **60.3%** | 1.74 | +22K |
| Dragon | GD | 10m | 55% | 50.0% | 2.59 | +786K |
| Dragon | NG | 3m | 55% | 45.2% | 1.60 | +446K |
| **ПОРТФЕЛЬ** | 4 тикера | — | — | — | — | **+633%** |

**Capital:** 200,000 -> 1,465,867
**MTM MDD:** 14.28% (цель ~15%) ✅
**Cash MDD:** 11.33%
**Trades:** 2810
**ROI за последний год:** +298.3%, MTM MDD 11.36%, Calmar 26.3

## Подбор риска

| IR/Dragon risk | ROI (2.5г) | MTM MDD | ROI (1y) |
|:--------------:|:----------:|:-------:|:--------:|
| 15% / 20% | +317% | 9.8% | +161% |
| 25% / 30% | +430% | 10.3% | +186% |
| 35% / 45% | +563% | 12.8% | +260% |
| **45% / 55%** | **+633%** | **14.3%** | **+298%** |

## Ключевые исправления (важно!)

1. **LOOK-AHEAD в Dragon исправлен** — portfolio_run.py: `bars_list: dh + [db]`
   - Было: tail на предыдущем баре, вход по его цене на текущем (идеальная цена)
   - Стало: tail на текущем баре, вход по его close
2. **paper_trader.py: M1 бары + resample detect** (было M5 — live не совпадал с бэктестом)
3. **PG: tf в params** (GD=10, NG=3, LKOH=5, SNGP=5)
4. **run_paper_trader.py:** expiry check fix (позиции в JSON)
5. **entry_time fix** в manage_positions

## Данные
- 33 ALLFUT контракта + полная M1 история (2024-2026) в CH
- 46 тикеров, ~17M баров

## Файлы
- strategies/dragon/scripts/portfolio_run.py — risk 45/55%, период 2024-01-01
- strategies/common/paper_trader.py — M1+resample, entry_time fix
- run_paper_trader.py — expiry fix
- futures.portfolio в PG — 4 стратегии с tf
