# 🔥 Checkpoint 150 — Reinvest + Stop Loss + Risk Reduction

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures

---

## Изменения

1. **Реинвест** — PG contracts=NULL (dynamic sizing по % капитала)
2. **Риск снижен** — RISK_PCT=0.02→0.01 (1% капитала на сделку)
3. **Стоп-лосс 0.7%** — добавлен hard SL в BrokerSim (был только timeout + trailing TP)

### Файлы

- `strategies/common/executor.py` — RISK_PCT 0.02→0.01
- `strategies/common/broker.py` — DEFAULT_STOP_LOSS=0.7, Position.stop_loss_pct, stop loss check в update()

## Результаты

**Параметры:** Stop Hunt, 5 tickers, 1% risk, 0.7% SL, timezone fix, trading hours, 100K, 18 мес:

```
Equity:   187,627,861 ₽
Return:   +187,527%
MDD:      2.02%
Trades:   4,989
WR:       55.1%
PF:       7.271
Avg win:  +79,036 ₽
Avg loss: -13,361 ₽
Sharpe:   1.740
```

## Состояние проекта (финальное)

- [x] PnL формула: `(exit-entry)/ms*sp*pct - TC` без *lot
- [x] PG ticker_specs: step_price per-contract (Si=1.0, GZ=1.0, RN=1.0)
- [x] PG portfolio: contracts=1 для paper trader (безопасно)
- [x] Timezone: IRK→MSK в engine.py
- [x] Off-hours фильтр в backtester.py
- [x] Stop loss: 0.7% в broker.py
- [x] Risk: 1% в executor.py
- [x] System cron: Stop Hunt paper trader, пн 10:00 MSK
- [x] MOEX Finam MT5: запущен под wine-finam
