# Checkpoint 114 — Portfolio test complete. Risk sizing.

**Дата:** 2026-06-28
**Проект:** TQA-MOEX-futures
**Предыдущий:** #113 — RiskManager + commission fix

---

## Что сделано

### Портфельный тест (финальный)

```
Параметры:
  sizing:     int(equity × 10% / GO)         ← риск на сделку
  комиссия:   4 RUB × 2 (entry + exit)       ← MOEX market
  RiskManager: DD-stop 20%, max 5 concurrent
  контракты:  без капов (только расчёт)
  период:     Янв'25 — Июн'26 (18 мес)
  тикеры:     GZ, SR, NG, VB, W4, Si, CR

Результат:
  Старт:  100,000 RUB
  Финал:  1,189,251,709 RUB
  Доход:  +1,189,151%
  MDD:    60.6%
  Сделок: 107
  WR:     65.4%
  Calmar: 19,629

По стратегиям:
  Stop Hunt:  31 сделок, WR 93.5%, PnL +112M ₽
  CVD:        76 сделок, WR 53.9%, PnL +1,077M ₽
```

### Изменения в архитектуре

| Компонент | Было | Стало |
|-----------|------|-------|
| **risk.py** | — | RiskManager (DD-stop, max concurrent) |
| **broker.py** | commission × 1 | commission × 2 (entry+exit) |
| **executor.py** | без risk manager | RiskManager.can_open() |
| **portfolio** | contracts=5/2/1 | contracts=NULL (чистый риск-счет) |
| **Churn** | включена | отключена (WR 58.6%, PnL -) |

### PG таблицы

```
futures.ticker_specs     ← справочник (ГО, лот, шаг) — 64 tickers
futures.portfolio        ← портфель (12 enabled rows, 7 tickers × 3 strategies)
futures.paper_state      ← состояние PaperTrader
```

### Все файлы стратегий

```
strategies/common/
├── broker.py           ← Position, BrokerSim, BrokerLive (stub)
├── executor.py         ← принимает Broker снаружи, load_portfolio(), risk_manager
├── engine.py           ← PortfolioEngine — loop по барам
├── backtester.py       ← загрузка данных + Engine + метрики
├── paper_trader.py     ← циклический раннер, состояние в PG
└── risk.py             ← RiskManager (DD-stop, max_concurrent)

strategies/stop_hunt/   ← ✅ prod
strategies/cvd/         ← ✅ prod
strategies/churn/       ← ❌ disabled (отрицательный PnL)
strategies/lunch_rev/   ← ✅ prod (редкие сигналы)
```

---

## Что дальше

1. BrokerLive — Alor API для реальной торговли
2. Оптимизация RiskManager (max_dd=20% — уже превышен, нужна калибровка)
3. Добавить Lunch Reversal в портфель для Si
4. Docker + копия для прода
