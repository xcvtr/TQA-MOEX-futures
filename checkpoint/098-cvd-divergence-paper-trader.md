---
title: "CVD divergence paper trader — live M5 через AlgoPack API"
checkpoint: 098
date: 2026-06-26
tags: [checkpoint, TQA-MOEX-futures, CVD, paper-trader, algopack, M5]
---

# Чекпойнт 098: CVD divergence paper trader

## Что сделано

Создан бумажный трейдер для CVD divergence стратегии на M5 фьючерсах MOEX.

### Архитектура

- **Данные:** AlgoPack fo tradestats REST API (не CH, не PG)
- **Ресемпл:** 1м → 5м через pandas
- **Сигнал:** CVD divergence (close.diff(20), cvd_cum.diff(20), quantile q=0.6)
- **Walk-forward:** train 180d / test 60d, пороги per-symbol
- **Entry:** лимитка по close сигнального бара, hold=1
- **Хранение:** ClickHouse (PG не работает — TimescaleDB broken)

### Таблицы (ClickHouse BD moex)

- `strategy_paper_trades` — сделки (id, ticker, direction, entry_price, exit_price, entry_time, exit_time, pnl_rub, status)
- `strategy_portfolio_state` — капитал (100K старт)

### Файлы

- `scripts/cvd_divergence_paper_trader.py` — основной скрипт (557 строк)
- `scripts/cvd_divergence_scanner.sh` — shell-обёртка для cron

### Тестовый прогон

```bash
# Dry-run — проверить сигналы без записи в БД
./scripts/cvd_divergence_scanner.sh --dry-run

# Catchup — проверить пропущенные сигналы за последние 2 дня
./scripts/cvd_divergence_scanner.sh --catchup --days 2

# Обычный запуск (будет писать в CH)
./scripts/cvd_divergence_scanner.sh
```

### Cron

```bash
hermes cron create \
  --name "cvd-divergence-scanner" \
  --schedule "*/5 9-23 * * 1-5" \
  --script "cvd_divergence_scanner.sh" \
  --deliver "local"
```

### Известные проблемы

1. **PG DDL не работает** — TimescaleDB extension broken на 10.0.0.64. Таблицы созданы в CH.
2. **MXI — фондовый индекс** — комиссия по нему 0.0132% (выше, чем NG/BR 0.0088%). В бэктесте использовали единую 0.0088%, для paper trader 0 — мейкер.
