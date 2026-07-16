---
title: "Dragon FINAL — sweep optimum + unified framework + paper trader"
checkpoint: 166
date: 2026-07-16
tags: [checkpoint, tqa-moex-futures, dragon, portfolio-final, framework]
---

# Checkpoint 166: Dragon FINAL — Unified Portfolio Framework

## Итоговые результаты

### Свип оптимума (2-4 тикера × 7 risk)
**ТОП-1: MM+GZ+SV, risk=7% → +168%, MTM MDD 12.4%**

| # | Тикеры | Risk | Доходность | MTM MDD |
|:-:|:-------|:----:|:----------:|:-------:|
| 1 | MM+GZ+SV | 7% | **+168%** | 12.4% |
| 2 | MM+SV+BR | 7% | +167% | 13.1% |
| 3 | MM+GZ+BR | 7% | +155% | 12.4% |
| 4 | MM+CR+BR | 7% | +151% | 9.8% |
| 5 | MM+NG+SV | 7% | +148% | 11.6% |

### Динамический портфель
Ядро: **MM** (GO=2.2K, PF=1.68). Тикеры добавляются по мере роста equity.

| Этап | Equity | Тикеры | Доходность |
|:----:|:------:|:-------|:----------:|
| 1 | 200K | MM,GZ,SV | **+168%** |
| 2 | 350K | +BR,NG | +150-170% |
| 3 | 600K | +CR,RN | +130-150% |
| 4 | 1M+ | все | +100-120% |

**CAGR ~100-120%** на долгосроке, MTM MDD 12-13%.

## Unified Framework

`strategies/common/portfolio_manager.py` — база для независимых стратегий.

- `StrategyEngine` — базовый класс
- `DragonStrategy` — реализация dragon
- `PortfolioManager` — распределяет капитал между стратегиями
- Через PG `STRATEGIES` можно включать/выключать стратегии

## Файлы

| Файл | Описание |
|:-----|:---------|
| `strategies/common/portfolio_manager.py` | Unified framework |
| `strategies/dragon/scripts/mtm_mdd.py` | MTM MDD backtest |
| `strategies/dragon/scripts/dynamic_portfolio.py` | Динамический портфель |
| `strategies/dragon/scripts/sweep_optimum.py` | Свип оптимума |
| `strategies/dragon/scripts/sim_years.py` | 5-летняя симуляция |
