---
title: "CVD divergence paper trader — live, кеш, системный cron, Hermes watchdog"
checkpoint: 098
date: 2026-06-26
tags: [checkpoint, TQA-MOEX-futures, CVD, paper-trader, algopack, M5]
---

# Чекпойнт 098: CVD divergence paper trader (обновлён)

## Что сделано

Бумажный трейдер для CVD divergence стратегии на M5 фьючерсах MOEX (NG, BR, Si, MXI).
Запущен в реальном времени через системный cron.

### Архитектура

- **Данные:** AlgoPack fo tradestats REST API (не CH, не PG)
- **Кеш:** SQLite `~/.hermes/data/cvd_paper/algopack_cache.db` — инкрементальный, каждая дата сохраняется сразу после загрузки
- **Дозагрузка:** параллельно 6 воркеров, 5 дней при каждом запуске (или 20 при холодном старте)
- **Ресемпл:** 1м → 5м через pandas
- **Сигнал:** CVD divergence (close.diff(20), cvd_cum.diff(20), quantile q=0.6)
- **Walk-forward:** train 120d (было 180, снижено т.к. 5м ресемпл + неторговые дни дают меньше календарных дней)
- **Entry:** по close сигнального бара, hold=1
- **Хранение:** ClickHouse (10.0.0.64, БД moex)
- **Комиссия:** 0 (мейкер), slippage 0.5 тика

### Таблицы (ClickHouse БД moex)

- `strategy_paper_trades` — сделки (id Int32, ticker, direction, entry_price, exit_price, entry_time, exit_time, pnl_rub, status, strategy='cvd_divergence')
- `strategy_portfolio_state` — капитал (strategy, capital, peak_capital, lots, updated_at)

### Файлы

| Файл | Назначение |
|------|-----------|
| `scripts/cvd_divergence_paper_trader.py` | Основной скрипт (~680 строк) |
| `scripts/cvd_paper_trader.sh` | Shell-обёртка для системного cron |
| `scripts/catchup_cache.py` | Инкрементальная дозагрузка кеша |
| `scripts/final_catchup.py` | Форсированная дозагрузка для Si/MXI |
| `~/.hermes/scripts/watchdog_cvd_paper.sh` | Hermes watchdog (состояние трейдера) |

### Cron

**Системный (основной):** каждые 5 мин Пн-Пт 07:00-23:50 IRKT
```
*/5 7-23 * * 1-5 /home/user/projects/TQA-MOEX-futures/scripts/cvd_paper_trader.sh
```

**Hermes (watchdog):** каждые 30 мин Пн-Пт
```bash
hermes cron create \
  --name "cvd-divergence-scanner" \
  --schedule "*/5 9-23 * * 1-5" \
  --script "cvd_divergence_scanner.sh" \
  --deliver "local"
```

Watchdog проверяет:
1. Наличие системного crontab записи
2. Наличие/свежесть SQLite-кеша
3. Последний запуск по timestamp в логе
4. Доступность CH и наличие записей в `strategy_paper_trades`

### Состояние кеша

| Символ | Баров | Последняя дата |
|--------|-------|----------------|
| NG | 26,624 | 25.06.2026 |
| BR | 26,437 | 25.06.2026 |
| Si | 22,221 | 25.06.2026 |
| MXI | 26,389 | 25.06.2026 |

### Исправленные проблемы

1. **Timeout на первом запуске** — 800+ последовательных HTTP запросов. Решение: SQLite-кеш + параллельная загрузка (6 воркеров). Первый запуск ~3 мин, последующие ~30 секунд.
2. **Бесконечный цикл на today** — `last_dt < today` вечно пытался загрузить сегодняшний день (данных нет). Решение: проверка `last_dt < yesterday`.
3. **CH таблица без id** — `strategy_paper_trades` создалась без колонки `id`. Решение: DROP + CREATE TABLE с `id Int32`.
4. **FutureWarning pandas** — `resample('5T')` → `resample('5min')`.

### Известные проблемы

1. **Si пороги** — p_thr=1961.2 (против NG=0.237, BR=2.95). Возможно из-за большой цены Si (50K+ пунктов) или некорректной логики diff(lk) для Si.
2. **PG DDL не работает** — TimescaleDB extension broken на 10.0.0.64. Всё в CH.
