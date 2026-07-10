---
title: "MTM DD, market hours gate, dashboard — финал бумажного трейдера"
checkpoint: 160
date: 2026-07-09
tags: [checkpoint, tqa-moex-futures, paper-trader, mtm-drawdown, dashboard]
---

# Checkpoint 160: Paper Trader — MTM DD, Market Hours, Dashboard

**Дата:** 2026-07-09
**Проект:** TQA-MOEX-futures

## Что сделано

### 1. MTM Drawdown (mark-to-market)
- Добавлена функция `calc_mtm_equity()` — расчёт unrealized PnL по открытым позициям
- PG: колонки `mtm_equity`, `mtm_peak` во всех `paper_state_*` таблицах
- `run_paper_trader.py` — выводит MTM DD в `--stdout` и предупреждение при >20%
- Дашборд: MTM DD карточки в каждой колонке

### 2. Market Hours Gate
- `paper_trader.py` — проверка `market_open` (MOEX 15:00-23:45 IRK)
- Вне торгов — только manage существующих позиций, новые сигналы не проверяются
- Лог: `MOEX market closed (IRK hour=N). Skipping new signals.`

### 3. Stale CVD Guard
- `get_volume_data()` — проверка возраста `tradestats_fo` (>30ч → dcvd_z=0)
- CVD не использует устаревшие объёмы

### 4. Исправление мёртвых стратегий
- **CVD** — убран хардкод `dcvd_z=0`, реальный расчёт из vol_b/vol_s
- **Impulse Return** — добавлены `close_hist`, `vol_hist` в bar_data
- **Stop Hunt** — работал, без изменений
- **JSON serialization** — `_json_safe()` конвертирует datetime → isoformat

### 5. Дашборд (scripts/dashboard.py)
- Исправлен `bars_undefined` — fallback `p.bars_held !== undefined ? ... : '—'`
- Добавлен `mtm_equity`, `mtm_peak`, `mtm_dd_pct` в API
- Добавлен unrealized PnL per position (текущая цена из CH + specs из PG)
- MTM DD карточки в каждой колонке

## Текущее состояние

| Инстанс | Cash Eq | MTM Eq | Cash DD | MTM DD | Позиции |
|:--------|:-------:|:------:|:-------:|:------:|:--------|
| portfolio | 200,000 | 199,057 | 0% | 0% | 2 |
| stop_hunt | 200,000 | 200,000 | 0% | 0% | 0 |
| impulse_return | 200,000 | 199,057 | 0% | 0% | 2 |

### Открытые позиции
- RN long @ 33,297 (impulse_return) — unrealized -1,114₽
- GZ short @ 9,771 (impulse_return) — unrealized +171₽

### Cron jobs (3 инстанса, все `last_status=ok`)
| Job | Стратегия | Расписание |
|:----|:---------:|:----------:|
| TQA-MOEX-futures paper trader (stop_hunt) | stop_hunt | `*/5` |
| impulse-return-paper-trader | impulse_return | `*/5` |
| moex-futures-portfolio-paper-trader | все | `*/5` |

## Изменённые файлы

| Файл | Изменение |
|:----|:----------|
| `strategies/common/paper_trader.py` | MTM DD, market hours, stale CVD guard, close_hist/vol_hist, _json_safe |
| `run_paper_trader.py` | MTM DD отображение, поддержка state-key |
| `scripts/dashboard.py` | MTM DD карточки, unrealized PnL, fix bars_undefined |
| `~/.hermes/scripts/run_moex_futures_paper.sh` | stop_hunt wrapper |
| `~/.hermes/scripts/pt_stop_hunt.sh` | Удалён |

## Для продолжения
- При сигналах cron пишет сюда
- Дашборд: http://10.0.0.60:8087/
- `python3 run_paper_trader.py --stdout` для диагностики
