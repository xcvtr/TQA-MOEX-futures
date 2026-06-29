# Checkpoint 128 — Final architecture: PRI=PG, STDBY=CH

**Дата:** 2026-06-29
**Проект:** TQA-MOEX-futures

---

## Архитектура

```
PRI (PG 10.0.0.60)                STDBY (CH 10.0.0.60)
  PaperTrader (cron 5 мин)          Backtester
  │                                   │
  ├── futures.prices (2 мес)         ├── tradestats_fo (18+ мес)
  │    43,779 bars                     все 64 tickera
  │    7 tickerov портфеля
  │    autopurge > 2 мес
  │
  ├── futures.portfolio (11 записей)
  ├── futures.ticker_specs (64)
  └── futures.paper_state
        capital + peak + positions

PG кластер: 3 ноды (10.0.0.60/63/64) — PRI/STDBY готов
```

## Данные в PG

| Тикер | Баров | Период | Средняя цена |
|-------|:-----:|--------|:-----------:|
| GZ | 6,799 | 28 апр — 19 июн | 12,004 |
| SR | 6,783 | 28 апр — 19 июн | 32,463 |
| NG | 6,814 | 28 апр — 19 июн | 3.02 |
| VB | 6,771 | 28 апр — 19 июн | 8,524 |
| W4 | 3,416 | 28 апр — 19 июн | 17,200 |
| Si | 6,597 | 28 апр — 19 июн | 74,575 |
| CR | 6,599 | 28 апр — 19 июн | 11.04 |

## Разделение

| Компонент | Данные | Справочники |
|-----------|--------|-------------|
| **PaperTrader** | PG prices | PG portfolio + ticker_specs |
| **Backtester** | CH tradestats_fo | PG portfolio + ticker_specs |
| **Loader** | CH (все) + PG (портфель) | — |
| **Dashboard** | PG paper_state + prices | PG portfolio |

## Расписание cron

| Джоб | Период | Часы |
|------|:------:|:----:|
| load_moex_prices.sh | каждые 5 мин | 10-23, будни |
| run_paper_trader.py | каждые 5 мин | 10-23, будни |

---

## Итог за сессию (28-29 июн)

- Архитектура: Engine → Executor → Broker → Backtester/PaperTrader
- Портфель в PG с весами
- Аудит: трейлинг, стопы, slippage, ликвидность, комиссия
- TRIZ fix: немедленное исполнение стопов
- Look-ahead fix: вход на open[i+1]
- PaperTrader: PG-only, catch-up, trailing state
- Loader: snapshot API, prefix mapping, autopurge
- Dashboard: systemd, live monitoring
- Данные: 2 мес в PG, полная история в CH
