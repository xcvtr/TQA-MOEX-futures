# 🔥 Checkpoint 147 — MOEX StepPrice Fix

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures
**Теги:** pnl-formula, moex-stepprice, bug-fix, lot-multiplier

---

## Что изменилось

### Критический баг: MOEX STEPPRICE — per-contract, не per-unit

**Обнаружение:** Сравнение PG `futures.ticker_specs` с CH `moex.securities` (сырые данные MOEX ISS).

MOEX `STEPPRICE` — это **рубли за один тик за один КОНТРАКТ**.
Формула ошибочно умножала ещё на `* lot`, завышая PnL в lot×.

| Тикер | MOEX: 1 tick | Было (с *lot) | Стало (без *lot) |
|-------|-------------:|--------------:|-----------------:|
| Si | 1.0₽ | 1,000₽ ❌ | 1.0₽ ✅ |
| RN | 1.0₽ | 100₽ ❌ | 1.0₽ ✅ |
| GZ | 1.0₽ | 100₽ ❌ | 1.0₽ ✅ |
| CR | 1.0₽ | 1,000₽ ❌ | 1.0₽ ✅ |
| GD | 7.72₽ | 7.72₽ ✅ (lot=1) | 7.72₽ ✅ |
| NG | 7.72₽ | 772₽ ❌ | 7.72₽ ✅ |

Si был "исправлен" ранее (PG sp=0.001 = MOEX 1.0 / 1000) — это маскировало баг.

### Исправленные файлы

| Файл | Было | Стало |
|------|------|-------|
| `strategies/common/broker.py` | `gross = ticks * sp * shares * lot * pct` | `gross = ticks * sp * shares * pct` |
| `strategies/common/executor.py` | Position(..., lot, pct) | Position(..., pct) |
| `strategies/cvd/prod/lib_cvd_divergence.py` | `pnl_rub = ticks * cost * lot * pct` | `pnl_rub = ticks * cost * pct` |
| `strategies/cvd/scripts/mtm_portfolio.py` | `mult = sp / ms * lot(tkr)` | `mult = sp / ms` |
| `scripts/scan_stop_hunt.py` | `(ex-ep)/ms*sp*lot*pct - TC` | `(ex-ep)/ms*sp*pct - TC` |
| `strategies/common/engine.py` | _pending dict (1 sig/ticker) | _pending list (multi sig/ticker) |

### PG изменения

- `futures.ticker_specs`: Si step_price `0.001` → `1.0` (MOEX standard)

---

## Результаты портфельного теста (Stop Hunt, 5 tickers)

**Параметры:** 1 контракт, TO=12, trailing 0.5/0.3%, 4₽ комиссия, open[i+1]+1 tick
**Данные:** CH tradestats_fo, Jan'25 — Jul'26 (18 мес), 100K капитал общий
**Инструмент:** `strategies/common/backtester.py` (исправленный)

```
┌──────────────────────────────────────────────────────────┐
│ STOP HUNT PORTFOLIO — 5 tickers, 1 contract, общий 100K │
├──────────────────────────────────────────────────────────┤
│ Equity:           11,112,148 ₽                           │
│ Return:             +11,012 %                            │
│ MDD:                     10.34 %                         │
│ Trades:                 10,411                           │
│ Win Rate:                 65.4 %                         │
│ Profit Factor:            3.846                          │
├──────────────────────────────────────────────────────────┤
│ CR (CNY):  1,738 сд, WR=57.9%, PnL=   +364,299 ₽        │
│ GD (GOLD): 1,888 сд, WR=62.5%, PnL= +3,071,286 ₽        │
│ GZ (GAZR): 2,385 сд, WR=70.1%, PnL= +1,115,933 ₽        │
│ RN (ROSN): 2,157 сд, WR=66.8%, PnL= +1,307,044 ₽        │
│ Si (USD):  2,243 сд, WR=67.4%, PnL= +5,153,586 ₽        │
└──────────────────────────────────────────────────────────┘
```

**11M с 100K за 18 мес — реалистично.** 1 контракт, 5 тикеров, без реинвеста (размер позиции не растёт).

### Исправлено в engine.py
- `_pending` теперь поддерживает **множественные сигналы на тикер** (list вместо single dict)
- Первая стратегия (по порядку) занимает тикер, остальные отбрасываются
- Это позволяет нескольким стратегиям (CVD + Stop Hunt) присутствовать в портфеле

---

## Питфоллы

1. **MOEX STEPPRICE — per-contract.** Всегда. Не умножать на lot.
2. **PG specs не консистентны.** Si sp был 0.001 (MOEX 1.0/1000), RN/GZ sp=1.0 (MOEX 1.0). Кто-то поделил Si, кто-то нет.
3. **CH securities — эталон.** Если сомневаешься в PG specs — проверь CH `moex.securities`.
4. **Бэктестер работает долго (3-5 мин).** 58K баров × 5 тикеров × Python loop.
5. **После изменения PnL формулы — проверить paper trader.** Он тоже использует PnL формулу (paper_trader.py).

---

## Состояние проекта

- ✅ PnL формула: `(exit-entry)/ms*sp*pct - TC` (без *lot) — везде
- ✅ PG Si sp=1.0 (MOEX)
- ✅ Engine._pending: multi-signal (CVD + Stop Hunt)
- ✅ Portfolio: contracts=1 для всех
- ✅ Бэктестер: per-ticker breakdown

## Файлы изменений

```
M strategies/common/broker.py              # -lot из _close_market
M strategies/common/executor.py            # -lot из Position()
M strategies/common/engine.py              # _pending list
M strategies/common/backtester.py          # by_ticker breakdown
M strategies/cvd/prod/lib_cvd_divergence.py # -lot из calc_pnl_rub
M strategies/cvd/scripts/mtm_portfolio.py  # mult = sp/ms
M scripts/scan_stop_hunt.py               # -lot из PnL
M reports/portfolio_stop_hunt_5tk.md       # результат
A checkpoint/147-moex-stepprice-fix.md
```
