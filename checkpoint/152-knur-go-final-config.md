# 🔥 Checkpoint 152 — КНУР GO + Финальная конфигурация

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures

---

## Изменения

### PG ticker_specs: GO для КНУР

С апреля 2025 действуют новые правила маржинального кредитования (Указание ЦБ №6681-У). Категории риска: КПУР → КСУР → КНУР.

**КНУР = exchange GO × 2.8** (из документа Финама: КНУР = КСУР × 1.4 = КПУР × 2 × 1.4)

Обновлены GO для всех 64 тикеров в PG `futures.ticker_specs`.

### executor.py
- MAX_LEVERAGE=10 удалён (MOEX — только ГО, не leverage)
- RISK_PCT: 0.02 → 0.01 (1%)

### run_backtest.py + visualize.py
- run_backtest.py: прогон тестера, сохранение equity + trades + summary в PG schema `backtest.*`
- run_backtest.py: run_id по дате (sh_200k_20260706_0813)
- visualize.py: поддержка `--run <id>` для чтения из PG (секунды вместо минут)
- visualize.py: ось Y в % доходности, не абсолют

### NEW SCHEMA
`backtest.equity_curve`, `backtest.trades`, `backtest.summary` — созданы в PG postgres.

---

## Финальный результат (все фиксы)

**Параметры:** Stop Hunt, 5 tickers, КНУР (×2.8 GO), пониженное ГО Финам, 1% риск, 0.7% SL, timezone fix, off-hours filter

```
200,000 → 7,155,912 ₽  (+3,478%)
MDD:     1.64%
WR:      54.5%  |  PF: 6.51
Trades:  6,619

Si: GO 34,834  (было 13,284)  leverage 2.9×
GZ: GO  5,796  (было 2,070)  leverage 2.3×
RN: GO  7,695  (было 7,512)  leverage 6.5×
GD: GO 83,885  (было 32,138) leverage 3.0×
CR: GO  3,643  (было 1,301)  leverage 0.3×
```

---

## История сессии

| Чекпойнт | Что сделано |
|:--------:|:------------|
| 146 | +*lot*pct (перебор) |
| 147 | −*lot, Si sp fix (правильно) |
| 148 | ✗ stock sp×lot (REVERT) |
| 149 | Timezone IRK→MSK, off-hours filter |
| 150 | SL 0.7%, risk 1% |
| 151 | MTM curves, visualize, PG save |
| **152** | **КНУР GO (×2.8), FINAL CONFIG** |

---

## Состояние

- [x] PnL формула: `(exit-entry)/ms*sp*pct - TC` без *lot
- [x] PG step_price: per-contract (Si=1.0)
- [x] PG go: КНУР ×2.8 от exchange
- [x] PG portfolio: contracts=1 (безопасно)
- [x] Timezone: IRK→MSK
- [x] Off-hours: filter
- [x] Stop loss: 0.7%
- [x] MAX_LEVERAGE: removed
- [x] visualize.py: fast PG path
- [x] run_backtest.py: save to PG
- [x] Backtest result in PG: `sh_200k_20260706_0813`
