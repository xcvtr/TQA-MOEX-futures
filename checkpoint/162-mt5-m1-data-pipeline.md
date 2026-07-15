---
title: "M1 данные из MT5 FINAM + универсальный backtest + tick M1"
checkpoint: 162
date: 2026-07-15
tags: [checkpoint, tqa-moex-futures, mt5, m1-bars, backtest]
---

# Checkpoint 162: M1 Data Pipeline — MT5 FINAM → PG + CH

## Что сделано

### 1. MT5 FINAM как источник данных
- Запущен второй portable MT5 (FINAM), счёт подключён
- Загружено **380,426 M1 баров** по 8 тикерам (с 2023-2024 до сегодня)
- Данные пишутся в **CH** (полная история, для backtest) и **PG** (live, autopurge 2mo)

### 2. Архитектура данных
```
MT5 FINAM → load_mt5_bars.py (каждую минуту) → CH moex.mt5_bars (full history)
                                              → PG futures.bars_1m (autopurge 60d)
```

### 3. Paper trader — разделение detect/tick
- **detect** (сигналы): M5 (каждые 5 мин), без изменений
- **tick** (TP/SL/trailing): **M1** (каждую минуту), через `--mode tick`
- Оба режима используют свежие M1 данные из PG

### 4. Универсальный backtest
- `strategies/common/backtest.py` — читает portfolio из PG, запускает все стратегии
- M1 бары из CH, detect на M5, tick на M1
- `--tickers NG,MM,GZ --days 365`

### 5. Дашборд
- 4 колонки: Stop Hunt, Impulse Return, Dragon 🐉, Portfolio All
- Метрики только в Portfolio All, остальные — только позиции

## Текущее состояние

### Активные стратегии (PG portfolio)

| Стратегия | Тикеры | Контракты |
|:----------|:------:|:---------:|
| stop_hunt | Si, GZ, CR, RN, GD | 1 |
| impulse_return | Si, GZ, CR, RN, GD | 1 |
| dragon 🐉 | **NG, MM, GZ** | **2** |

### Cron jobs
| Job | Расписание | Назначение |
|:----|:----------:|:-----------|
| mt5_bars_loader | `* * * * *` | M1 бары из MT5 → CH + PG |
| moex-futures-portfolio-tick-m1 | `* 15-23 * * 1-5` | Управление позициями M1 |
| moex-futures-portfolio-detect | `*/5 15-23 * * 1-5` | Поиск сигналов M5 |

### Данные в CH
| Таблица | Строк | Источник |
|:--------|:-----:|:---------|
| moex.mt5_bars | **380K** | MT5 FINAM (M1 OHLCV) |
| moex.tradestats_fo | 21M | AlgoPack (fallback) |
| moex.prices_5min | — | ISS (fallback) |

## Изменённые файлы
| Файл | Изменение |
|:-----|:----------|
| `strategies/common/paper_trader.py` | `--mode tick/detect`, `get_latest_bars` PG→CH→fo→prices |
| `strategies/common/backtest.py` | Создан — универсальный backtest |
| `strategies/dragon/scripts/backtest.py` | Переписан на M1 |
| `scripts/load_mt5_bars.py` | Создан — dual-write CH + PG |
