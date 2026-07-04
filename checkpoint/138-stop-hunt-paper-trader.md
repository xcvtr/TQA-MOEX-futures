---
title: "Stop Hunt paper trader — универсальный"
checkpoint: 138
date: 2026-07-04
tags: [checkpoint, tqa-moex-futures, paper-trader]
---

# Checkpoint 138 — Stop Hunt paper trader

**Дата:** 2026-07-04

## Что сделано

### 1. Универсальный paper trader

`strategies/common/paper_trader.py` — работает со всеми стратегиями через STRATEGY_MAP.

**Логика:**
1. Загружает портфель из PG `futures.portfolio` (все enabled записи)
2. Для каждого тикера: загружает последние 50 баров из CH `tradestats_fo`
3. Для каждой стратегии в портфеле: вызывает `check_signal(bar_data, ticker)`
4. Управляет открытыми позициями: trailing TP, stop loss, timeout
5. Сохраняет состояние в PG `futures.paper_state`
6. Закрытые сделки → `futures.paper_trades`

**PG таблицы:**
| Таблица | Структура |
|---|---|
| `futures.paper_state` | capital, equity, peak, positions_json, bar_idx, next_id |
| `futures.paper_trades` | ticker, strategy, direction, entry/exit price, pnl |

### 2. Cron

| Крон | Расписание | Тип |
|---|---|---|
| Stop Hunt paper trader | каждые 5 мин, 10:00-18:00, пн-пт | no_agent |

### 3. Портфель текущий

| Тикер | Лот | 1pt | Стратегии | Вес |
|---|---|---|---|---|
| Si (USDRUB) | 1000 | 1000 ₽ | stop_hunt + cvd | 1.2 / 1.0 |
| GZ (Газпром) | 100 | 100 ₽ | stop_hunt + cvd | 1.5 / 1.0 |
| NG | 100 | 771 ₽ | stop_hunt | 0.8 |
| W4 (Пшеница) | 1 | 10 ₽ | stop_hunt | 0.8 |

### Ключевые метрики (backtest, 18 мес)

Stop Hunt: 53.5% WR, 1.75 PF, TO=12
CVD: 48.7% WR (на грани шума)
