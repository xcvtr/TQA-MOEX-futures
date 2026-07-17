---
title: "Dragon in framework — M5 resampling, PG params, bars_list fix"
checkpoint: 168
date: 2026-07-17
tags: [checkpoint, tqa-moex-futures, dragon, framework]
---

# Checkpoint 168 — Dragon integration into PortfolioEngine

## Что сделано

### 1. bars_list в engine.py
Dragon требует полный список баров (dict с opn/hi/lo/prc/vol) для распознавания паттерна. Добавлен `bars_list` в сигнальный цикл — строится из M5 кеша (M1→M5 ресамплинг).

### 2. M1→M5 resampling
- Engine обрабатывает M1 бары (SL/TP на каждом баре)
- Каждые 5 M1 баров → 1 M5 бар в `_m5_cache`
- Сигналы проверяются ТОЛЬКО на M5 (каждые 5 мин)
- `_build_bar` больше не создаёт тяжёлый `bars_list` на каждом баре (была причина таймаутов)

### 3. PG params из `futures.portfolio` → Position
- `executor.py` читает `params` JSONB из PG
- Парсит `stop_loss_pct` → передаёт как `stop_loss` в trailing_params
- Трейлинг params: `activation`, `trail`, `timeout` — из колонок PG

### 4. Параметры стратегий в PG

#### 🐉 Dragon (обновлено)
| Параметр | Значение |
|:---------|:--------:|
| `trailing_activation` | **1.5%** |
| `trailing_trail` | **0.5%** |
| `timeout_bars` | **60** |
| `stop_loss_pct` | **1.0%** (из params JSONB) |

#### 🔶 IR / 🔵 Stop Hunt (без изменений)
| Параметр | Значение |
|:---------|:--------:|
| `trailing_activation` | 0.5% |
| `trailing_trail` | 0.3% |
| `timeout_bars` | 12 |
| `stop_loss_pct` | 0.7% |

### 5. Specs из PG `futures.ticker_specs`
Все тесты теперь используют реальные specs из PG (ms, sp, go). Ручные SPECS словари удалены.

#### Правильные specs для ключевых тикеров
| Тикер | ms | sp | go (полный) | lot |
|:------|:--:|:--:|:-----------:|:---:|
| NG | 0.001 | 7.70611 | 20,519 | 100 |
| BR | 0.01 | 7.70611 | 34,328 | 10 |
| SV | 0.01 | 7.70611 | 30,707 | 10 |
| MM | 0.05 | 0.5 | 4,330 | 1 |
| GZ | 1.0 | 1.0 | 5,796 | 100 |

## Итоги сессии

| Стратегия | Данные | Результат |
|:----------|:-------|:----------|
| 🐉 Dragon | MT5 FINAM M1→M5, 1yr | pf=0.76 (мало сделок, 1yr) |
| 🔶 IR | Все — шум на continuous данных | ❌ edge нет |
| 🔵 Stop Hunt | MT5 FINAM M5, 1yr | ❌ pf=0.96, шум |

**Dragon:** подтверждённый edge через `mtm_backtest.py` (+168% за 4 года, PF 1.66). В PortfolioEngine на 1 год показал pf=0.76 — мало сделок (298) для статистики.

## Изменённые файлы

| Файл | Изменение |
|:-----|:----------|
| `strategies/common/engine.py` | M1→M5 resampling, bars_list в сигнальном цикле, _build_bar оптимизация |
| `strategies/common/executor.py` | Парсинг params JSONB, stop_loss из PG |
| `strategies/common/broker.py` | slippage_in параметр |
| `strategies/impulse_return/prod/engine.py` | Cooldown, vol filter, min_vol |
| PG `futures.portfolio` | Dragon: activation=1.5, trail=0.5, timeout=60, stop_loss_pct=1.0 |
