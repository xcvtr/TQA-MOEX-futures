---
title: "Dragon 5-min detect + M1 tick = +1224% годовых"
checkpoint: 170
date: 2026-07-22
tags: [checkpoint, tqa-moex-futures, dragon, m1, 5min, reinvest]
---

# Checkpoint 170: Dragon — 5-min detect, M1 tick = +1224%

## Ключевое открытие
**Detect ≠ Tick.** Detect на 5-min (resample из M1), tick/SL/TP на M1.

## Результат
**Капитал: 200K → 2.6M (+1224%) за 365д, MDD 38%, риск 2%**

| Тикер | Сделок | WR | PnL | PF | MDD |
|:------|:-----:|:--:|----:|:--:|:---:|
| BR | 650 | 47.5% | +1,574K | 1.30 | 37.5% |
| MM | 183 | 53.6% | +528K | **1.94** | 18.8% |
| GD | 491 | 49.3% | +140K | 1.15 | 37.8% |
| GZ | 322 | 47.5% | +135K | 1.21 | 28.4% |
| RN | 332 | 47.0% | +69K | 1.13 | 35.8% |

## Активный портфель
- **Dragon**: GZ, BR, MM, GD, RN — реинвест 2%
- Остальные стратегии отключены

## Ключевые изменения
- `AGENTS.md` — обновлён под новую архитектуру
- `strategies/common/backtest.py` — добавлен `--reinvest`
- `strategies/stop_hunt/prod/engine.py` — lookback=40, retrace=0.1
- `strategies/impulse_return/prod/engine.py` — impulse_bars=12
