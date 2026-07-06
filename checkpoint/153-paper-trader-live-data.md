# 🔥 Checkpoint 153 — Paper Trader Live Data Fix

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures

---

## Критический баг: paper trader не видел живых данных

Paper Trader использовал `tradestats_fo` через CH `10.0.0.64`. Таблица не обновлялась с **19 июня 2026**.

Весь день 6 июля paper trader работал с пустыми данными → 0 сигналов.

## Фиксы

| Файл | Баг | Фикс |
|------|-----|------|
| `paper_trader.py` | CH_HOST = `10.0.0.64` | `10.0.0.60` |
| `paper_trader.py` | Чтение `tradestats_fo` (stuck Jun 19) | Чтение `prices_5min` + агрегация OHLC (`max(hi), min(lo)`) |
| `paper_trader.py` | PG_HOST = `10.0.0.64` | `10.0.0.60` |
| `paper_trader.py` | entry_price = `prc_prev` + ms | `prc` + ms (latest close + 1 tick) |
| `run_paper_trader.sh` | `python3` (system, без clickhouse_connect) | `.venv/bin/python3` |

## Состояние данных

| Таблица | Последние данные | Статус |
|---------|:----------------:|:------:|
| `tradestats_fo` (AlgoPack) | **19 июн 2026** | ❌ не обновляется |
| `bars` | **1 июл 2026** | ❌ не обновляется |
| `prices_5min` | **22:55 6 июл** | ✅ live, но snapshot-style |
| `prices_5min` после агрегации | 55 баров с real hi/lo | ✅ работает |

## Дашборд

Создан `scripts/dashboard.py` — http.server на порту 8087.
Показывает equity, позиции, сделки, equity curve из backtest.
Обновление каждые 15 сек.
Process: background (proc_328cde257561).

## Cron

Stop Hunt paper trader: `*/5 15-23 * * 1-5` (10:00-18:45 MSK, только основная+вечерняя сессии)
Утренняя сессия (11:50-14:50 IRK) отфильтрована в бэктесте — не тестировалась, не торгуем.

## Результаты сессии (полные)

| Чекпойнт | Что |
|:--------:|:----|
| 146 | +*lot*pct (перебор) |
| 147 | −*lot, Si sp fix |
| 148 | ✗ stock sp×lot (REVERT) |
| 149 | Timezone, off-hours filter |
| 150 | SL 0.7%, risk 1% |
| 151 | MTM, visualize, PG save |
| 152 | КНУР GO ×2.8, final config |
| **153** | **Paper trader live data fix** |
