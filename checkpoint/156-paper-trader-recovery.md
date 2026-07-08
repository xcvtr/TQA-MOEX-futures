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

| Метрика | stop_hunt | impulse_return | portfolio |
|:--------|:---------:|:--------------:|:---------:|
| Capital | 200,000₽ | 200,000₽ | 200,000₽ |
| Equity | 200,000₽ | 200,000₽ | 200,000₽ |
| DD | 0.0% | 0.0% | 0.0% |
| Сделок | 0 | 0 | 0 |
| Открыто | 0 | 0 | 0 |

### Активный портфель (PG futures.portfolio WHERE enabled=true)

| Тикер | Стратегии |
|:-----:|:---------|
| Si | stop_hunt, cvd, impulse_return |
| GZ | stop_hunt, cvd, impulse_return |
| CR | stop_hunt, cvd, impulse_return |
| RN | stop_hunt, cvd, impulse_return |
| GD | stop_hunt, cvd, impulse_return |

### Cron jobs (3 инстанса)

| Job | Стратегия | state-key | Скрипт | Статус |
|:----|:---------:|:---------:|:-------|:------:|
| TQA-MOEX-futures paper trader (stop_hunt) | stop_hunt | stop_hunt | `run_moex_futures_paper.sh` | ✅ ok |
| impulse-return-paper-trader | impulse_return | impulse_return | `pt_impulse_return.sh` | ✅ ok |
| moex-futures-portfolio-paper-trader | все (без фильтра) | portfolio | `pt_portfolio.sh` | ✅ ok |

### Данные
- CH `prices_5min` на 10.0.0.60 — свежие (01:00 IRK 9 июля, после вечерней сессии)
- CH `tradestats_fo` — 2026-07-07 (AlgoPack, обновляется раз в день)
- Сигналов нет (рынок закрыт, MOEX откроется в 15:00 IRK)

## Изменённые файлы

| Файл | Изменение |
|:----|:----------|
| `strategies/common/paper_trader.py` | `save_state()` — сделки теперь в `paper_trades_{state_key}`, не хардкод `futures.paper_trades` |
| `run_paper_trader.py` | Полностью переписан: subprocess → `paper_trader.py` с `--state-key`, читает из правильных таблиц |
| `~/.hermes/scripts/run_moex_futures_paper.sh` | Переписан: запускает `run_paper_trader.py --strategy stop_hunt --state-key stop_hunt` |
| `~/.hermes/scripts/pt_stop_hunt.sh` | Удалён (дублировал функционал) |

### 🐛 Найденные и исправленные баги

| # | Баг | Фикс |
|:-:|:----|:------|
| 1 | `save_state()` хардкодил `INSERT INTO futures.paper_trades` — сделки не разделялись по state-key | Динамическая таблица: `tbl_trades = 'paper_trades_' + STATE_KEY` |
| 2 | `run_paper_trader.py` читал из `futures.paper_state` (без суффикса) — таблица не существует | Читает из `paper_state_{state_key}` |
| 3 | Cron запускал `pt_stop_hunt.sh` вместо `run_moex_futures_paper.sh` — мёртвый код | Удалён pt_stop_hunt.sh, cron обновлён |
| 4 | `paper_trades_stop_hunt` не существовала в PG | Создана `LIKE paper_trades` |

## Для продолжения
- При появлении сигналов cron будет писать сюда
- `python3 run_paper_trader.py --stdout` для диагностики
- `strategies/common/paper_trader.py` — ядро, не менять без проверки всех потребителей

### 🚨 Правила работы (добавлены в AGENTS.md)
1. **Линтер обязателен** — write_file/patch авто-линтуют .py/.json/.yaml/.toml. Не подавлять ошибки.
2. **Дважды проверять перед отчётом** — файл создан, скрипт работает, данные свежие, cron `last_status=ok`, нет мусора в git.
3. **Не гадать** — верифицировать через прямой запрос (SQL/CURL) перед докладом.
