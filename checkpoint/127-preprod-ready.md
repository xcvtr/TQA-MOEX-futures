# Checkpoint 127 — Pre-prod ready: PaperTrader PG-only, PRI/STDBY

**Дата:** 2026-06-29
**Проект:** TQA-MOEX-futures

---

## Что сделано

### PaperTrader — чистый PG, без CH

- `use_pg=True` → `self.ch = None`, данные только из `futures.prices`
- CH импорт опциональный (`try/except ImportError`)
- Состояние в `futures.paper_state` (capital + peak + positions)
- catch_up() — прогон всей истории при старте
- tick() — только новый бар

### Cron (каждые 5 мин, будни 10-23)

| Джоб | Расписание | Делает |
|------|-----------|--------|
| `load_moex_prices.sh` | `*/5` | Snapshot ISS → PG prices |
| `run_paper_trader.py` | `+2 мин` | Сигналы → управление позициями |

### Portfolio (PG)

7 тикеров, 2 стратегии, веса:

```
GZ:  Stop Hunt (1.5) + CVD (1.0)
SR:  Stop Hunt (1.0) + CVD (0.8)
NG:  Stop Hunt (0.8)
VB:  Stop Hunt (1.0)
W4:  Stop Hunt (0.8)
Si:  Stop Hunt (1.2) + CVD (1.0)
CR:  Stop Hunt (0.8) + CVD (0.6)
```

### Данные

| Хранилище | Что | Назначение |
|-----------|-----|-----------|
| PG `futures.prices` | 109 баров × 7 tickеров | PaperTrader (pre-prod) |
| PG `futures.portfolio` | 11 записей | Конфиг |
| PG `futures.paper_state` | capital + peak + positions | Состояние |
| PG `futures.ticker_specs` | 64 tickera | Справочник |
| CH `tradestats_fo` | все tickеры, 18+ мес | Backtester |

### Сервисы

| Сервис | Статус |
|--------|:------:|
| Dashboard :8080 | systemd, автостарт |

---

## Что дальше

1. BrokerLive — Alor API
2. Docker (когда понадобится)
3. Оптимизация параметров (z-пороги, trailing)
