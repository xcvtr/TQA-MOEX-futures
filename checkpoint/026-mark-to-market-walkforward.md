# 026 — Mark-to-Market + Walk-Forward: система учёта unrealized PnL

## Что сделано

### Mark-to-Market (V3.1)
- Добавлен параметр `use_mtm: bool = True` в `simulate_adaptive_portfolio()`
- `_total_equity()` теперь учитывает unrealized PnL открытых позиций через `last_price`
- DD-лимит срабатывает при просадке с учётом незакрытых убытков
- `last_price` обновляется для текущего тикера на каждом сигнале (перед DD-проверкой)
- `current_price` вычисляется 1 раз в начале итерации, заменён во всех блоках (rollover, eviction, DD force close)

### Walk-Forward Stability Check
- Написан `scripts/walkforward_stability.py`
- Разбивает 7093 сигнала на 4 хронологических folds по 85 дней
- Для каждой из 72 комбинаций проверяет: прибыльность во всех 4 folds
- Результат: **0 из 72** комбинаций прошли (подтверждение хрупкости PF)

### Лучшие комбинации (суммарно по folds)
| # | mu | mc | tm | sl | total% | min% | neg folds |
|---|----|----|----|----|--------|------|-----------|
| 1 | 0.20 | 5 | 0.20 | 0.01 | +349.3% | −21.8% | 1 (fold1) |
| 2 | 0.20 | 8 | 0.20 | 0.01 | +349.3% | −21.8% | 1 (fold1) |
| 3 | 0.10 | 8 | 0.20 | 0.02 | +91.4% | −17.4% | 2 (fold2, fold3) |

### Ключевое открытие
mark-to-market не решает проблему хрупкости полностью — `last_price` обновляется только для тикеров с новым сигналом. Для позиций без сигнала unrealized PnL = 0. Нужен переход на bar-level симуляцию с OHLCV-барами всех 47 тикеров для честного расчёта equity и стопов.

## Файлы
- `trading_bot/portfolio.py` — V3.1 с MTM
- `scripts/walkforward_stability.py` — анализ стабильности

## Что дальше
- [ ] Bar-level симуляция с OHLCV по всем 47 тикерам
- [ ] Честный trailing stop на каждом баре
- [ ] Реальный walk-forward с bar-level
