# Checkpoint 134 — Live 5-min bars via AlgoPack. PaperTrader clean start.

**Дата:** 2026-06-30
**Проект:** TQA-MOEX-futures

---

## Что сделано

### Исправлены 5-мин бары

AlgoPack `tradestats()` возвращает смесь:
- **Настоящие 5-мин бары** на `:00`, `:05`... минутах (OHLC меняется, малый объём)
- **Дневные снапшоты** на остальных (OHLC дня, накопленный объём)

Фикс: фильтр `tradetime.minute % 5 == 0` → только настоящие бары.

12,360 баров за 30 июня загружено. OHLC меняется между барами:

```
11:50 → O=11675 H=11675 L=11675 C=11675 V=1
11:55 → O=10555 H=10566 L=10554 C=10562 V=790
12:00 → O=10561 H=10561 L=10538 C=10551 V=566
```

### Cron

| Джоб | Расписание | Статус |
|------|:----------:|:------:|
| AlgoPack bars | `*/5 0-4,11-23 * * *` | ✅ ok |
| PaperTrader | `2,7,12.. 0-4,11-23 * * *` | ✅ ok |
| ISS snapshot | `*/5 0-4,11-23 * * *` | ✅ ok |

17 часов покрытия (06:50-23:50 MSK) + выходные.

### PaperTrader

- Состояние сброшено: 100K, 0 сделок
- `tick()` — проверка последнего бара каждые 5 мин
- `catch_up()` — доступен для ручного запуска

---

## PG таблицы

| Таблица | Данные | Autopurge |
|---------|--------|:---------:|
| `futures.prices` | OHLCV + vol_b/vol_s/oi | 2 мес |
| `futures.futoi` | FIZ/YUR OI | 2 мес |
| `futures.portfolio` | 11 записей | — |
| `futures.paper_state` | capital + peak + positions | — |

---

## Файлы

- `strategies/common/algopack_bars.py` — загрузчик (исправлен фильтр баров)
- `strategies/common/paper_trader.py` — catch_up + tick
- `strategies/common/executor.py` — RISK_PCT=0.02
- `run_paper_trader.py` — только tick()

---

## Cron

| Расписание | Часы UTC+8 | MSK |
|:----------:|:----------:|:---:|
| `0-4,11-23` | 11:00-04:00 | 06:00-23:00 |
