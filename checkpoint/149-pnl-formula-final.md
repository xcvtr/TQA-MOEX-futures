# 🔥 Checkpoint 149 — PnL Formula Final

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures
**Теги:** pnl-formula, final, stepprice-revert

---

## Что произошло

**Окончательная формула PnL (после трёх итераций):**

```python
pnl = (exit - entry) / min_step * step_price * pct - commission
```

**Без `* lot`.** MOEX `STEPPRICE` — всегда ₽ за тик за контракт, для всех типов фьючерсов (валютные, акционные, товарные). Цены в ClickHouse для акционных фьючерсов хранятся **per-contract**, не per-share.

### История ошибок в этой сессии

| № | Действие | Результат |
|:-:|:---------|:----------|
| 146 | Добавил `*lot*pct` в broker.py | PnL завышен в lot× для currency (Si 1000×) |
| 147 | Убрал `*lot` из broker. **Правильно для currency.** Si sp: 0.001→1.0 | **11M** — правильно! |
| 148 | ✗ Умножил stock sp на lot в PG | **251M** — завысил GZ/RN в 100× |
| 149 | ✗ Откатил PG, оставил sp=1.0 | **~7.6-11M** — снова правильно ✅ |

### PG revert

Умножение step_price на lot_volume для 15 акционных тикеров отменено. Все step_price = 1.0 (MOEX per-contract).

---

## Финальные результаты

**Common backtester** (5 tickers, 1 contract, 100K, 18 мес):

```
Equity:     ~11,000,000 ₽
Return:      ~+11,000%
MDD:         ~10.3%
WR:          ~65.4%
PF:          ~3.85
Trades:      ~10,400
```

**bt_5t.py** (simpler, standalone):

```
SPECS: GZ=1.0, Si=1.0, RN=1.0, GD=77.06, CR=1000.0
Trades=11051 PnL=7,605K WR=55.6% PF=2.03
```

---

## Состояние проекта (после сессии)

- [x] PnL формула: `(exit-entry)/ms*sp*pct - TC` — без `*lot`
- [x] PG `futures.ticker_specs`: step_price per-contract для всех (stock sp=1.0)
- [x] PG `futures.portfolio`: contracts=1 для всех enabled
- [x] `strategies/common/broker.py` — без `*lot`
- [x] `strategies/common/executor.py` — Position без `lot`
- [x] `strategies/common/engine.py` — _pending list (multi-strategy)
- [x] `strategies/common/backtester.py` — by_ticker breakdown
- [x] `strategies/common/paper_trader.py` — без `*lot`
- [x] `strategies/cvd/prod/lib_cvd_divergence.py` — без `*lot`
- [x] `strategies/cvd/scripts/mtm_portfolio.py` — без `*lot`
- [x] `scripts/scan_stop_hunt.py` — hosts .60, без `*lot`
- [x] `scripts/bt_5t.py` — hosts .60, CR asset CNY, без `*lot`
- [x] System cron: `*/5 15-23 * * 1-5` → paper trader

## Проверка PnL (MOEX specs)

| Тикер | sp | ms | 1pt = sp/ms | MOEX stepprice | Статус |
|-------|:--:|:--:|:-----------:|:--------------:|:------:|
| Si | 1.0 | 1.0 | 1.0₽ | 1.0₽ | ✅ |
| GZ | 1.0 | 1.0 | 1.0₽ | 1.0₽ | ✅ |
| RN | 1.0 | 1.0 | 1.0₽ | 1.0₽ | ✅ |
| GD | 7.7 | 0.1 | 77.0₽ | 7.72₽×10 | ✅ |
| NG | 7.7 | 0.001 | 7,700₽ | 7.72₽×1000 | ✅ |
| CR | 1.0 | 0.001 | 1,000₽/pt | 1.0₽ per 0.001 | ✅ |
| Eu | 1.0 | 1.0 | 1.0₽ | 1.0₽ | ✅ |
