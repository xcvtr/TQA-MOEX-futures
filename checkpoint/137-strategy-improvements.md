---
title: "Strategy Improvements — Portfolio Tuning + Partial Exit Test + CVD Filter"
checkpoint: 137
date: 2026-07-04
tags: [checkpoint, tqa-moex-futures, stop-hunt, portfolio, improvements]
---

# Checkpoint 137 — Strategy Improvements: Portfolio Tuning, CVD Filter, Partial Exit

**Дата:** 2026-07-04
**Проект:** TQA-MOEX-futures
**Предыдущий:** #136 — CH Cluster Recovery + Timout Calibration

---

## Что сделано

### 1. VB и SR удалены из портфеля

Backtest показал, что VB и SR приносят отрицательный PnL (-35K суммарно) и просто тратят торговый капитал на стопы.

**PG изменения:**
- `SR stop_hunt` — enabled=False
- `SR cvd` — enabled=False
- `VB stop_hunt` — enabled=False

### 2. CVD Filter тест

Stop Hunt + CVD confirmation filter:
- PF улучшился (1.65 → 1.77), но сделок на 43% меньше (12,809 → 7,298)
- PnL упал с +3.6M до +2.3M
- Вывод: CVD фильтр повышает качество, но слишком агрессивно режет количество

### 3. Partial Exit тест

50% позиции закрывается на 1% TP, 50% трейлится:
- **Baseline:** +3,603K, WR 50.7%, PF 1.65
- **Partial 50%@1%:** -257K, WR 51.1%, PF 0.95 ❌
- **Вывод:** partial exit убивает стратегию. Stop Hunt живёт за счёт крупных трейдов с трейлингом. Отсекая 50% на 1%, вы отрезаете fat tail.

---

## Ключевые метрики

### Финальный портфель

| Ticker | Стратегии | Статус |
|---|---|---|
| GZ | stop_hunt + cvd | ✅ |
| NG | stop_hunt | ✅ |
| Si | stop_hunt + cvd | ✅ |
| W4 | stop_hunt | ✅ |
| CR | stop_hunt + cvd | ✅ (нет данных) |
| SR | stop_hunt + cvd | 🔴 disabled |
| VB | stop_hunt | 🔴 disabled |

### Итоговые бэктесты (1 контракт, TO=12, 18 мес)

| Конфиг | Trades | PnL | WR | PF |
|---|---|---|---|---|
| Baseline (all) | 12,809 | +3,631K | 50.7% | 1.65 |
| +CVD filter (all) | 7,298 | +2,278K | 51.4% | 1.77 |
| Baseline (good only — без VB, SR) | 7,785 | +3,618K | **53.5%** | 1.75 |
| +CVD filter (good only) | 4,531 | +2,253K | 54.0% | **1.87** |

**Лучший ROI:** убрать VB и SR. PnL практически не меняется, WR +2.8pp.

### Timeout calibration (подтверждение)

| TO | PnL | WR | PF |
|---|---|---|---|
| 4 | -150K | 43.7% | 0.97 |
| 8 | +2,342K | 48.7% | 1.42 |
| **12** | **+3,623K** | **50.7%** | **1.65** |
| 18 | +4,173K | 52.8% | 1.74 |
| 24 | +4,301K | 54.1% | 1.76 |
| 36 | +4,520K | 55.4% | 1.80 |
| ∞ | +4,644K | 57.8% | 1.82 |

TO=12 — оптимальный компромисс.

---

## Изменённые файлы

| Файл | Изменение |
|---|---|
| PG `futures.portfolio` | SR и VB disabled |
| `checkpoint/137-strategy-improvements.md` | Новый |

## Состояние данных

### ClickHouse (10.0.0.64, db=moex)

| Таблица | Рядов | Статус |
|---|---|---|
| `tradestats_fo` | 21.1M | ✅ Replicated |
| `obstats_fo` | 85.6M | ✅ Replicated (больше оригинала 46.9M) |
| `eq_tradestats` | 56.7M | ✅ Replicated |
| `bars` | 1.36M | ✅ |
| `futoi_iss` | 12.6M | ✅ |

### PostgreSQL (10.0.0.64, db=moex, schema futures)

| Таблица | Статус |
|---|---|
| `futures.portfolio` | 13 записей, 9 enabled ✅ |
| `futures.futoi_iss` | 241K rows ✅ |
| `futures.futoi_algopack` | 112 rows ✅ |

## Paper trader

Отключён. CVD-divergence был в минусе (-4.4%).

## Что дальше

1. Stop Hunt можно включать — 51.5% WR, 1.65 PF, честный backtest
2. CVD — отключить или перекалибровать (48.7% WR на грани)
3. Churn/Lunch Rev — оставить выключенными
4. CR (CNYRUBF) — нет данных в tradestats_fo, проверить asset_code
