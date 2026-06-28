# Checkpoint 112 — Архитектура: Backtester + PaperTrader, портфель в PG

**Дата:** 2026-06-28
**Проект:** TQA-MOEX-futures
**Предыдущий:** #111 — PaperTrader

---

## Что сделано

### Полная перестройка архитектуры

```
                           PG
                    ┌──────────────┐
                    │ futures      │
                    │  portfolio   │
                    │  ticker_specs│
                    │  paper_state │
                    └──────┬───────┘
                           │
┌──────────┐         ┌─────▼──────┐         ┌───────────┐
│ Backtester│         │  Executor  │         │   Broker  │
│ ← все бары│         │  капитал   │────────►│  Sim|Live │
└──────────┘         │  портфель  │         │  trailing │
                     └─────▲──────┘         └───────────┘
┌──────────┐               │
│PaperTrade│  ← тик (1 бар) │
│ save_state│───────────────┘
└──────────┘
```

### Новые файлы

| Файл | Назначение |
|------|-----------|
| `strategies/common/broker.py` | Position + BrokerSim (trailing from params) + BrokerLive stub |
| `strategies/common/executor.py` | Принимает Broker снаружи, load_portfolio() из PG |
| `strategies/common/engine.py` | PortfolioEngine — loop по барам |
| `strategies/common/backtester.py` | Загрузка данных + Engine + метрики |
| `strategies/common/paper_trader.py` | Циклический раннер, состояние в PG |

### PG таблицы

```sql
futures.portfolio — портфель (17 rows)
  ticker, strategy, enabled, contracts, weight, params (JSONB),
  trailing_activation, trailing_trail, timeout_bars

futures.paper_state — состояние PaperTrader
  key (capital, positions), value (JSON)
```

### Портфель (7 тикеров × 4 стратегии)

| Тикер | GO | Контр | Стратегии |
|-------|:--:|:-----:|-----------|
| GZ | 2,070 | 5 | StopHunt + CVD + Churn |
| SR | 6,105 | 2 | StopHunt + CVD + Churn |
| NG | 8,126 | 2 | StopHunt + Churn |
| VB | 1,351 | 5 | StopHunt + Churn |
| W4 | 2,199 | 5 | StopHunt + Churn |
| Si | 13,283 | авто | StopHunt + CVD + LunchRev |
| CR | 1,301 | авто | StopHunt + CVD |

### Удалено

- `public.moex_ticker_specs` (дубль)
- `futures.strategy_cvd_portfolio` (CVD-only legacy)
- `strategies/common/trailing_tp.py` (логика в BrokerSim)
- `scripts/` (277 файлов) → архив
- `reports/` (183 legacy файла) → архив
- `configs/` → архив
- `data/` (6.3GB), `docs/`, `trading_bot/`, `logs/`, `screenshots/`, `templates/`, `updates/` → архив
- 107 корневых legacy-скриптов → архив

### Git

- 7 коммитов (chkpt 108a–111)
- 506 файлов изменено, ~312K строк удалено
- Проект: 7.3 GB → ~2 MB

---

## Что дальше

1. Тестирование портфеля (Backtester.run)
2. BrokerLive — Alor API
3. Docker + копия для прода
