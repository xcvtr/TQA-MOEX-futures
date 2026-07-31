# Checkpoint 197: Портфель ALLFUT — LKOH + SNGP IR, +211% ROI, MDD 10.4%

**Дата:** 2026-07-31
**Теги:** checkpoint, portfolio, allfut, ir

## Результаты (2.5 года данных ALLFUT, common pool)

| Стратегия | Тикер | TF | Risk | WR | PF | PnL |
|:----------|:------|:--:|:----:|:--:|:--:|----:|
| IR | LKOH (Лукойл) | 5m | 15% | **63.2%** | 2.67 | +5.3K |
| IR | SNGP (Сургут-п) | 5m | 15% | **74.1%** | 1.63 | +2.7K |
| Dragon | GD | 10m | 20% | 56.6% | 3.55 | +219K |
| Dragon | NG | 3m | 20% | 46.8% | 1.72 | +234K |
| **ПОРТФЕЛЬ** | 4 тикера | — | — | — | — | **+211%** |

**Capital:** 200,000 -> 621,749
**MTM MDD:** 10.36%
**Trades:** 1130

## Ключевые изменения
1. **ALLFUT контракты (33 шт)** — активированы и загружены с полной историей (2024-2026, M1)
   - Ограничение баров в MT5 снято (Tools -> Options -> Charts)
2. **IR sweep по 30 тикерам** (2.5 года): топ LKOH (WR до 91.9%), SNGP (88.2%), TATN, HANG
3. **GAZR не прошёл** на 2.5 годах — удалён из портфеля
4. **Портфель:** LKOH 5m + SNGP 5m (IR) + GD + NG (Dragon)

## Файлы
- strategies/dragon/scripts/portfolio_run.py — обновлён
- futures.portfolio в PG — 4 стратегии
- Все данные в moex.mt5_continuous (ALLFUT с 2024)
