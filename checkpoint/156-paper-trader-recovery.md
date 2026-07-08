---
title: "Paper trader восстановлен — fix ImportError + cron resumed"
checkpoint: 156
date: 2026-07-08
tags: [checkpoint, tqa-moex-futures, paper-trader, fix]
---

# Checkpoint 156: Paper Trader Recovery

**Дата:** 2026-07-08
**Проект:** TQA-MOEX-futures
**Суть:** Починка бумажного трейдера MOEX фьючерсов — `run_paper_trader.py` был сломан, cron paused 4 дня.

## Что было

### ❌ run_paper_trader.py — ImportError
```
ImportError: cannot import name 'PaperTrader' from 'strategies/common/paper_trader'
```
Скрипт импортировал класс `PaperTrader`, который был удалён при рефакторинге `strategies/common/paper_trader.py` в функциональный стиль (`run_tick()`, `manage_positions()` и т.д.). `run_paper_trader.py` не обновили.

### ❌ Cron paused с 4 июля
`TQA-MOEX-futures paper trader` (job_id: `34f5c876fa11`) стоял на паузе 4 дня. Последний запуск — 2026-07-04 04:59.

## Что сделано

### 1. Переписан run_paper_trader.py
- Полностью переписан с использованием `run_tick()` из `strategies/common/paper_trader.py`
- **Silent-till-event паттерн:** сравнивает количество сделок и состояние PG до/после тика
- Выводит только:
  - ✅❌ Новые закрытые сделки (ticker, direction, strategy, pnl₽, reason)
  - 📌 Открытие новых позиций
  - ⚠️ Просадка >20%
- `--stdout` для принудительного вывода статуса (диагностика)
- Exit code 1 при exception

### 2. Создана cron-обёртка
- `~/.hermes/scripts/run_moex_futures_paper.sh`
- Использует `.venv/bin/python3` (с clickhouse_connect)
- `no_agent=true`

### 3. Cron возобновлён
- Job `34f5c876fa11` — unpaused, state=scheduled
- Расписание: `*/5 0-4,11-23 * * *` (каждые 5 мин в торговые часы MOEX)
- Доставка: origin (этот чат)
- Silent-till-event: если нет сделок — тишина

## Текущее состояние

| Метрика | Значение |
|---------|:--------:|
| Capital | 200,000₽ |
| Equity | 200,000₽ |
| Peak | 200,000₽ |
| Сделок | 0 |
| Открыто позиций | 0 |
| DD | 0.0% |

### Активный портфель (PG futures.portfolio WHERE enabled=true)

| Тикер | Стратегии |
|:-----:|:---------:|
| GZ | stop_hunt, cvd |
| Si | stop_hunt, cvd |
| CR | stop_hunt, cvd |
| RN | stop_hunt, cvd |
| GD | stop_hunt, cvd |

### Данные
- CH `prices_5min` на 10.0.0.60 — свежие (последний бар 10:00+)
- Сигналов нет (stop_hunt и cvd возвращают None — рынок не даёт)

## Изменённые файлы

| Файл | Изменение |
|:----|:----------|
| `run_paper_trader.py` | Полностью переписан (ImportError → silent-till-event) |
| `~/.hermes/scripts/run_moex_futures_paper.sh` | Создан (cron wrapper, no_agent) |

## Для продолжения
- При появлении сигналов cron будет писать сюда
- `python3 run_paper_trader.py --stdout` для диагностики
- `strategies/common/paper_trader.py` — ядро, не менять без проверки всех потребителей
