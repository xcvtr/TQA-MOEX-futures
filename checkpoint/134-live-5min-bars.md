# Checkpoint 134 (обновлён) — Live 5-min bars via AlgoPack. Fix: filter second=0.

**Дата:** 2026-07-01
**Проект:** TQA-MOEX-futures

---

## Что сделано

### Исправлен фильтр баров (v2)

Дополнительно: `second == 0`. AlgoPack tradestats возвращает 2 типа данных:

| Тип | Минута | Секунда | Объём | OHLC |
|-----|:------:|:-------:|:-----:|:----:|
| 5-мин бар ✅ | %5 == 0 | 00 | малый | меняется между барами |
| Дневной снапшот ❌ | любая | >0 | накопленный | OHLC дня |

Первый фильтр (`minute % 5 == 0`) пропускал `00:00:36` (минута 0, секунды 36). Добавлен `second == 0`.

### Количество баров в PG

| Тикер | 30 июня (чистые) | Фильтр |
|-------|:----------------:|:------:|
| Si | 179 | ✅ second=0 |
| GZ | 178 | ✅ |
| SR | 179 | ✅ |
| CR | 179 | ✅ |
| NG | 179 | ✅ |
| VB | 179 | ✅ |
| W4 | 93 | ✅ |

### Cron

| Джоб | Расписание | Доставка | Статус |
|------|:----------:|:--------:|:------:|
| AlgoPack bars | `*/5 0-4,11-23 * * *` | local | ✅ ok |
| PaperTrader | `2,7,12.. 0-4,11-23 * * *` | сюда (если сделка) | ✅ ok |
| Snapshot | `*/5 0-4,11-23 * * *` | local | ✅ ok |

### PaperTrader

- **Состояние:** 100K, 0 сделок (сброшено)
- `tick()` — проверка последнего бара
- **Оповещения** — только при новой сделке (тихо, пока сигнала нет)

---

## Изменённые файлы

- `strategies/common/algopack_bars.py` — filter: `minute % 5 == 0 AND second == 0`
- `run_paper_trader.py` — output only on new trades / DD warning

## PG

| Таблица | Данные |
|---------|--------|
| `futures.prices` | 30 июня: 179×7=1,253 bars чистых. 1 июля: нет (сессия с 11:00) |
| `futures.paper_state` | capital=100000, peak=100000, positions=[] |

## Cron скрипты

```
~/.hermes/scripts/
├── load_algopack_bars.sh  → .venv/ strategies/common/algopack_bars.py
├── load_moex_prices.sh    → python3 loader.py --load-portfolio-prices
└── load_all_prices.sh     → python3 loader.py --load-prices
```

PaperTrader — прямой `cd ... && python3 run_paper_trader.py` (в cron dжобе, без .sh обёртки).

## Время торгов (MSK)

| Сессия | MSK | UTC+8 (сервер) |
|--------|:---:|:--------------:|
| Утренняя | 06:50-09:50 | 11:50-14:50 |
| Основная | 09:50-19:00 | 14:50-00:00 |
| Вечерняя | 19:00-23:50 | 00:00-04:50 |
| Выходного дня | 09:50-19:00 | Сб-Вс 14:50-00:00 |
