---
title: "Dragon стратегия — адаптация для MOEX, backtest, paper trader"
checkpoint: 161
date: 2026-07-13
tags: [checkpoint, tqa-moex-futures, dragon, paper-trader]
---

# Checkpoint 161: Dragon Strategy — MOEX Futures

## Что сделано

### 1. Dragon engine для MOEX (strategies/dragon/prod/engine.py)
- Адаптирован из TQA-crypto `dragon_detect.py`
- Паттерн: шея→коррекция→горбы→хвост (LONG/SHORT)
- Параметры под MOEX: impulse=0.3%, retrace=70%, hump=0.1%
- Поля данных: `bars_list` (весь DataFrame), `prc` (текущая цена)

### 2. Sweep по всем 64 тикерам MOEX (1 год tradestats_fo)
- 18 тикеров с PF>1 из 61 протестированных
- Топ по Score (PnL/MDD):
  1. NG — PnL +3.8M, MDD 5.7%, PF 3.75
  2. MM — PnL +625K, MDD 2.1%, PF 2.17
  3. GZ — PnL +271K, MDD 1.2%, PF 1.90

### 3. Портфельный backtest с MTM DD
- Портфель: NG + MM + GZ
- Комиссия: 4₽ round-trip (MOEX SCALPERFEE + Finam 0.45)
- КНУР ×0.5 (ГО в CH moex.securities — свежие, сегодня)
- MTM DD: 9.5% при 1 контракте → 19% при ×2 контрактах
- PnL: +4.7M/год (×1) → +9.4M/год (×2)

### 4. Paper trader запущен
- PG portfolio: NG, MM, GZ с contracts=2
- Cron: `moex-futures-portfolio-paper-trader` (`*/5 15-23 * * 1-5`)
- Другие стратегии (stop_hunt, cvd, impulse_return) — без изменений, contracts=1

## Текущее состояние

| Стратегия | Тикеры | Contracts | Статус |
|:----------|:------:|:---------:|:-------|
| stop_hunt | Si, GZ, CR, RN, GD | 1 | ✅ |
| cvd | Si, GZ, CR, RN, GD | 1 | ✅ |
| impulse_return | Si, GZ, CR, RN, GD | 1 | ✅ |
| **dragon** 🐉 | **NG, MM, GZ** | **2** | ✅ **NEW** |

### Cron jobs
| Job | Стратегия | Расписание | Статус |
|:----|:---------:|:----------:|:------:|
| moex-futures-portfolio-paper-trader | все (SH+CVD+IR+Dragon) | `*/5 15-23 * * 1-5` | ✅ |

## Изменённые файлы
| Файл | Изменение |
|:-----|:----------|
| `strategies/dragon/prod/engine.py` | Создан — check_signal() для MOEX |
| `strategies/dragon/scripts/backtest.py` | Создан — backtest с tradestats_fo |
| `strategies/dragon/scripts/sweep.py` | Создан — sweep по 64 тикерам |
| `strategies/common/paper_trader.py` | Добавлен dragon в STRATEGY_MAP, bars_list в bar_data |
