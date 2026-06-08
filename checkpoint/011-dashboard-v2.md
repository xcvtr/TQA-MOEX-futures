# Checkpoint 011 — Dashboard v2 + Open Architecture

**Дата:** 2026-06-08
**Проект:** TQA-MOEX

## Что сделано

### 1. MOEX Demo Dashboard v2
Новый дашборд на FastAPI + Plotly.js, порт **:5090**.
Архитектура: `trading_bot/dashboard_v2/`

```
dashboard_v2/
├── core/
│   ├── registry.py      — регистр стратегий и рынков (открытая архитектура)
│   ├── models.py        — Signal, Position модели
│   └── statistics.py    — WR, PF, DD, equity curve
├── adapters/
│   ├── moex_adapter.py       — загрузка OHLCV + OI из moex DB
│   ├── moex_strategies.py    — авторегистрация 4 стратегий
│   ├── crypto_adapter.py     — заглушка (TODO)
│   └── forex_adapter.py      — заглушка (TODO)
├── routers/
│   ├── live.py          — GET /api/live/positions
│   ├── backtest.py      — GET /api/backtest/run + /api/backtest/strategies
│   ├── portfolio.py     — GET /api/portfolio/stats
│   └── data.py          — GET /api/data/freshness, /api/bars
├── frontend/
│   └── index.html       — 4 вкладки: Live, Backtest, Portfolio, Data
├── __init__.py          — app factory
└── serve.py             — uvicorn entry
```

### 2. Открытая архитектура
- **registry.py** — две точки расширения:
  - `register_strategy(StrategyInfo)` — добавить стратегию
  - `register_market(MarketInfo)` — добавить рынок
- Новая стратегия = 10 строк кода, ноль изменений в дашборде
- Crypto/Forex заглушки готовы к заполнению

### 3. Бэктесты подтверждены
| Стратегия | Топ тикер | OOS WR | Сигналов |
|:----------|:---------:|:------:|:--------:|
| VWAP | GZ | 58.7% | 1399 |
| Reversion | NM | 83.3% | 12 |
| VS | HS | 58.3% | 12 |
| OB | SBERF | 0% | 1 (stale data) |

### 4. План
- `docs/plans/2026-06-08-demo-dashboard-v2.md` — полный дизайн дашборда

### 5. Верификация
- Cross-check VWAP: 0 errors, verified signal-by-signal
- Все 4 стратегии считаются через API
- Frontend отдаётся (Plotly.js)
- NO look-ahead: подтверждено

## Файлы

| Файл | Статус |
|:-----|:------:|
| `trading_bot/dashboard_v2/` (12 файлов) | ✅ новый |
| `trading_bot/new_strategies.py` | ✅ новый |
| `docs/backtest/` (5 файлов) | ✅ новые |
| `docs/plans/2026-06-08-demo-dashboard-v2.md` | ✅ новый |
| `docs/plans/2026-06-08-four-new-strategies.md` | ✅ новый |

## Что дальше
- Включить дашборд v2 как основной (:5090)
- Обновить фронтенд index.html для interactivity
- При накоплении сделок — мониторинг Rolling WR
- Крипта/Форекс — архитектура готова, добавить при необходимости
