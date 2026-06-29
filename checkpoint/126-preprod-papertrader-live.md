# Checkpoint 126 — Pre-prod: PaperTrader live, dashboard, loader, portfolio clean

**Дата:** 2026-06-29
**Проект:** TQA-MOEX-futures

---

## Состояние

### Портфель (PG futures.portfolio)

7 тикеров × 2 стратегии = 11 активных записей:

| Тикер | Стратегии | Вес | Статус |
|-------|-----------|:---:|:------:|
| GZ | Stop Hunt + CVD | 1.5/1.0 | ✅ |
| SR | Stop Hunt + CVD | 1.0/0.8 | ✅ |
| NG | Stop Hunt | 0.8 | ✅ |
| VB | Stop Hunt | 1.0 | ✅ |
| W4 | Stop Hunt | 0.8 | ✅ |
| Si | Stop Hunt + CVD | 1.2/1.0 | ✅ |
| CR | Stop Hunt + CVD | 0.8/0.6 | ✅ |
| Churn | — | — | ❌ отключён |
| Lunch Rev | — | — | ❌ отключён |

### Архитектура

```
MOEX ISS API (marketdata snapshot)
     │
     ├── loader (каждые 15 мин) → PG futures.prices (7 портфель, 2 мес)
     │
     └── PaperTrader (каждые 15 мин) → PG futures.paper_state
           │
           └── catch_up() при старте → все 100+ баров
               tick() → только новый бар
```

### Данные

| Хранилище | Данные | Назначение |
|-----------|--------|-----------|
| CH tradestats_fo | Все 64 тикера, 18+ мес | Backtester |
| PG futures.prices | 7 тикеров портфеля, ~103 бара | PaperTrader |
| PG futures.portfolio | 11 записей | Конфиг |
| PG futures.paper_state | capital + peak + positions | Состояние |

### Сервисы

| Сервис | Порт | Статус |
|--------|:----:|:------:|
| Dashboard | 8080 | systemd, автостарт |

### Cron

| Джоб | Расписание | Тип |
|------|-----------|:---:|
| TQA-MOEX-futures portfolio prices | 0,15,30,45 10-23 * * 1-5 | no_agent |
| TQA-MOEX-futures paper trader | 2,17,32,47 10-23 * * 1-5 | terminal |

### Результаты бэктеста (последние, реалистичные)

```
7 тикеров | Stop Hunt + CVD | Янв-Июн 2026
100K → 201K (+101%), 107 сделок, WR 64.5%, MDD 23.4%
```

---

## Что изменилось (chkpt 120-125)

- **Loader**: snapshot API вместо candles, prefix mapping (SiU6→Si)
- **PG prices**: backfill 700 баров из CH, live обновление каждые 15 мин
- **PaperTrader**: use_pg, catch_up(), trailing state save/restore, best_abs
- **Portfolio weight**: влияет на sizing (GZ stop_hunt=1.5)
- **RiskManager**: отображается в дашборде (current DD, статус)
- **Dashboard**: systemd сервис, weight, RM status, auto-refresh
- **Lunch Reversal**: отключён
- **PaperTrader runner**: уведомления при сделках и DD>20%

---

## Что дальше

1. BrokerLive — Alor API
2. Docker + копия для прода
3. Оптимизация trailing/z-порогов
