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
- **Walk-forward:** train 120d (было 180)
- **Entry:** по close сигнального бара + 3 тика **в сторону сигнала** (AGGRESSIVE_TICKS=3), hold=1
- **Выход:** по close следующего 5м бара
- **Хранение:** ClickHouse (10.0.0.64, БД moex)
- **Комиссия:** 0 (мейкер — лимитки на MOEX), slippage 0.5 тика

### Почему AGGRESSIVE_TICKS=3

На M5 барах **нельзя смоделировать исполнение лимитки** — неизвестно, сколько раз цена касалась уровня close за бар. Сдвиг на N тиков в сторону сигнала страхует от неисполнения.

Проведён грид `wf_divergence_shift_grid.py` (7 сдвигов 0-5 тиков):

| Сдвиг | WR | Net PnL | DD | Calmar |
|-------|----|---------|----|--------|
| 0 | 66.2% | +292.9M | 0.67% | 449.7 |
| 1 | 66.2% | +292.6M | 0.68% | 447.5 |
| **3** | **65.3%** | **+291.9M** | **0.68%** | **443.0** |
| 5 | 64.6% | +291.2M | 0.69% | 438.6 |

Разница между 0 и 5 тиков — всего 0.6% PnL. Стратегия устойчива к цене входа.  
**3 тика** — разумный компромисс: почти гарантирует исполнение лимитки при −0.3% доходности.

### Таблицы (ClickHouse БД moex)

- `strategy_paper_trades` — сделки (id Int32, ticker, direction, entry_price, exit_price, entry_time, exit_time, pnl_rub, status, strategy='cvd_divergence')
- `strategy_portfolio_state` — капитал (strategy, capital, peak_capital, lots, updated_at)

### Файлы

| Файл | Назначение |
|------|-----------|
| `scripts/cvd_divergence_paper_trader.py` | Основной скрипт (~706 строк) |
| `scripts/cvd_paper_trader.sh` | Shell-обёртка для системного cron |
| `scripts/wf_divergence_shift_grid.py` | Грид сдвига лимитки (7 вариантов) |
| `scripts/catchup_cache.py` | Инкрементальная дозагрузка кеша |
| `scripts/final_catchup.py` | Форсированная дозагрузка |
| `~/.hermes/scripts/watchdog_cvd_paper.sh` | Hermes watchdog |

### Cron

**Системный (основной):** каждые 5 мин Пн-Пт 07:00-23:50 IRKT
```
*/5 7-23 * * 1-5 /home/user/projects/TQA-MOEX-futures/scripts/cvd_paper_trader.sh
```

**Hermes (watchdog):** `CVD paper trader watchdog` — `*/30 8-23 * * 1-5`, проверяет:
1. Наличие системного crontab записи
2. Наличие/свежесть SQLite-кеша
3. Последний запуск по timestamp в логе
4. Доступность CH и наличие записей в `strategy_paper_trades`

### Состояние кеша

| Символ | Баров | Последняя дата |
|--------|-------|----------------|
| NG | 26,624 | 25.06.2026 |
| BR | 26,473 | 25.06.2026 |
| Si | 22,221 | 25.06.2026 |
| MXI | 26,389 | 25.06.2026 |

### Исправленные проблемы

1. **Timeout на первом запуске** — SQLite-кеш + параллельная загрузка (6 воркеров)
2. **Бесконечный цикл на today** — проверка `last_dt < yesterday`
3. **CH таблица без id** — DROP + CREATE TABLE с `id Int32`
4. **FutureWarning pandas** — `resample('5T')` → `resample('5min')`
5. **Неисполнение лимиток** — добавлен AGGRESSIVE_TICKS=3 (сдвиг entry_price на 3 тика в сторону сигнала)

### Известные проблемы

1. **Si пороги** — p_thr=1961.2 (против NG=0.237, BR=2.95). Возможно из-за большой цены Si (50K+ пунктов).
2. **PG DDL не работает** — TimescaleDB extension broken на 10.0.0.64. Всё в CH.
