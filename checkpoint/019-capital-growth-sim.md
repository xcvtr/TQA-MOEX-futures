# Checkpoint 019 — Capital Growth Simulation (100K → walk-forward)

## Что сделано

Создана и запущена симуляция разгона депозита 100K RUB на исторических данных (2 года) с walk-forward оптимизацией риск-менеджмента.

### Архитектура

`scripts/capital_growth_sim.py` — самодостаточный скрипт:
1. **Сбор сигналов** — все 5 стратегий (production конфиги, 730 дней)
2. **Walk forward** — 2 фолда, rolling window (train→test)
3. **Grid search** — 72 комбинации параметров риск-менеджмента
4. **Симуляция с блокировкой ГО** — capital = free_cash + locked_margin

### Risk-модель

Параметры оптимизации:
- `margin_usage` — доля капитала на 1 сделку [0.05, 0.1, 0.15, 0.2, 0.25, 0.3]
- `max_concurrent` — макс. одновременных позиций [1, 2, 3]
- `max_dd_limit` — стоп торговли при просадке [0.15, 0.20, 0.25, 0.30]

ГО блокируется при входе (`capital -= contracts * go`), возвращается при закрытии.

### Результаты

| Метрика | Fold 1 | Fold 2 | Combined |
|:--------|:------:|:------:|:--------:|
| Train сигналов | 25,835 | 25,389 | — |
| Test сигналов | 28,563 | 15,552 | 44,115 |
| Train return | +∞% | +19,528% | — |
| **Test return** | **+2.0%** | **-39.6%** | **-38.4%** |
| **Max DD** | 37.6% | 70.6% | 70.6% |
| Params | mu=0.1, mc=2, dd=0.15 | mu=0.05, mc=2, dd=0.2 | — |

### Ключевое открытие

**97.6% сигналов (53K из 54K) — VWAP Deviation.** VWAP ловит редкие, но огромные гаповые движения:
- SR VWAP: топ-сделка = +8.1% (+245K RUB с 1 контракта)
- Avg return = +0.07%/сделку, но распределение тяжелохвостое

Walk-forward ломается: train находит параметры под гапы, test без гапов — слив.

### Файлы

- `scripts/capital_growth_sim.py` — скрипт симуляции
- `docs/plans/capital-growth-sim.md` — план
- `docs/plans/capital_growth/results.json` — полные результаты
- `docs/plans/capital_growth/equity_curve.csv` — equity кривая
- `docs/plans/capital_growth/summary.txt` — текстовый отчёт
- `docs/plans/capital_growth/optimal_params.csv` — лучшие параметры

### Что дальше

Проблема: VWAP доминирует портфель. Варианты:
- [ ] Убрать VWAP из разгона (оставить OB, VS, Reversion, OI Div)
- [ ] VWAP с фильтром (только |return_pct| > 1%)
- [ ] Раздельные субпортфели
- [ ] Режимный подход (VWAP только в тренде)
