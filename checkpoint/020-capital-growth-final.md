# Checkpoint 020 — Capital Growth: Final Result (без VWAP)

## Итог

**100 000 → 1 103 954 руб (+1004%), maxDD 29.5%, 646 сделок, 2 года.**

Walk-forward валидация подтвердила консистентность:

| Фолд | Train | Test | Параметры |
|:----:|:-----:|:----:|:----------|
| Fold 1 | +85% (DD 30.8%) | **+87%** (DD 29.5%) | mu=0.25, mc=3, dd=0.30 |
| Fold 2 | +389% (DD 20.5%) | **+489%** (DD 23.0%) | mu=0.15, mc=3, dd=0.20 |

## Архитектура

`scripts/capital_growth_sim.py`:
1. **Сбор сигналов** — 4 стратегии (без VWAP): OB, VS, Reversion, OI Div. ~15K сигналов за 730 дней
2. **Walk forward** — 2 фолда, rolling window
3. **Grid search** — Calmar ratio (return/maxDD). 54 комбинации: mu∈[0.03-0.15], mc∈[1-3], dd∈[0.15-0.25]
4. **Симуляция с блокировкой ГО** — capital = free_cash + locked_margin. DD break = полная остановка

## Ключевые решения

- **VWAP исключён** — 97% сигналов, хвостовые гапы ломают walk-forward
- **OB max_signal_age=999999** — для исторического сбора всех сигналов
- **Calmar ratio** — штрафует за просадку сильнее, чем raw return
- **DD hard stop** — break при превышении лимита, не continue

## Файлы

- `scripts/capital_growth_sim.py` — скрипт симуляции
- `docs/plans/capital_growth/results.json` — полные результаты
- `docs/plans/capital_growth/equity_curve.csv` — 653 шага equity
- `docs/plans/capital_growth/summary.txt` — текстовый отчёт
- `docs/plans/capital_growth/optimal_params.csv` — параметры по фолдам

## Git

Изменения в `scripts/capital_growth_sim.py` — major rewrite: GO locking, Calmar score, скорректированный grid, DD hard stop.
