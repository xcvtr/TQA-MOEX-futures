# 🔥 Checkpoint 146 — PnL Formula Fix

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures
**Теги:** pnl-formula, bug-fix, lot-pct, broker, mtm-portfolio

---

## Что изменилось

### PnL формула — критический баг

Обнаружен и исправлен баг в расчёте PnL: формулы переводили price_diff → RUB **без множителей `* lot * pct`**.

**Правильная формула:**
```
pnl_rub = (exit - entry) / min_step * step_price * lot * pct * contracts - commission * contracts
```

### Исправленные файлы

| Файл | Баг | Фикс |
|------|-----|------|
| `strategies/common/broker.py` | `gross = ticks * step_price * shares` — без `*lot* pct` | `gross = ticks * step_price * shares * lot * pct`. Position.__init__ принимает `lot=` и `pct=` |
| `strategies/common/executor.py` | Не передавал lot/pct в Position | Загружает `lot_volume` и `pct` из PG → передаёт в Position |
| `strategies/cvd/prod/lib_cvd_divergence.py` | `pnl_rub = pnl_ticks * tick_cost - slippage` | `pnl_rub = pnl_ticks * tick_cost * lot * pct - slippage`. Добавлены TICK_LOT, TICK_PCT |
| `strategies/cvd/scripts/mtm_portfolio.py` | `mult = lot(tkr)` — без step_price/min_step. **Si PnL был завышен 1000×** (10,000₽ вместо 10₽ за тик) | `mult = sp / ms * lot(tkr)` — исправлено во всех 4 местах |
| `scripts/scan_stop_hunt.py` | `pnl = (exit-entry)/ms*sp - TC` — без `*lot* pct`. Ещё: PG хост был `10.0.0.64` (diff specs) | `pnl = (exit-entry)/ms*sp*lot*pct - TC`. PG хост синхронизирован |

### Position sizing

- **executor.py**: приоритет `futures.portfolio.contracts` перед динамическим sizing
- **PG portfolio**: `contracts=1` для всех enabled стратегий (было NULL — размер по ~2% капитала, что убивало equity на CR/CR с их большим лотом)

---

## Результаты Stop Hunt Scan (36 тикеров, исправленная формула)

**Параметры:** Open[i+1]+1 tick slippage, trailing TP 0.5%/0.3%, timeout 12 bars, commission 4₽, Oct'2024 — Jun'2026, 1 contract (reinvest от equity)

┌──────┬────────┬─────────┬──────┬──────┬──────────┬──────┐
│ #    │ Тикер  │ Инструм │ Сдел │ WR%  │ Return%  │ MDD% │
├──────┼────────┼─────────┼──────┼──────┼──────────┼──────┤
│ 1    │ **Si** │ USDRUB  │ 2,215│ 54.0%│+1,016%   │ 19.2%│
│ 2    │ **GZ** │ GAZPROM │ 2,268│ 54.2%│+347%     │ 13.5%│
│ 3    │ **NG** │ N.Gas   │ 2,122│ 54.3%│+492%     │ 24.3%│
│ 4    │ **W4** │ Wheat   │ 170  │ 67.1%│+393%     │ 19.7%│
│ 5    │ **LK** │ Lukoil  │ 2,432│ 54.2%│+578%     │ 35.7%│
│ 6    │ **Eu** │ Euro    │ 2,357│ 52.7%│+334%     │ 26.5%│
│ 7    │ **SR** │ Sber    │ 2,459│ 52.5%│+121%     │ 10.3%│
│ 8    │ **RN** │ Rosneft │ 2,138│ 54.2%│+133%     │ 21.5%│
└──────┴────────┴─────────┴──────┴──────┴──────────┴──────┘

### Проверка PnL (1 pt = X₽ per contract)

- Si: `1/1*0.001*1000*1.0 = 1₽` ✅
- GZ/RN: `1/1*1.0*100*1.0 = 100₽` ✅
- Eu: `1/1*1.0*1000*1.0 = 1,000₽` ✅
- NG: `0.001/0.001*7.7*1*1.0 = 7.7₽` ✅
- CR: `1/0.001*1.0*1000*1.0 = 1,000,000₽` (высокое значение из-за lot=1000 × ms=0.001)

---

## Питфоллы (на будущее)

1. **Старая PG specs (10.0.0.64) vs рабочая (10.0.0.60):** На .64 Si step_price=1.0, на .60=0.001. scan_stop_hunt.py подключался к .64, из-за чего баг формулы `*lot` компенсировался ошибочным sp. После перевода на .60 обе ошибки открылись.
2. **Engine._pending блокирует стратегии:** Первая стратегия (CVD) захватывает pending для ticker'а, вторая (Stop Hunt) не может войти. Нужно или ticker+strategy ключ, или standalone scan per strategy.
3. **CR (CNY/RUBF) с lot=1000 и ms=0.001:** 1 pt = 1,000,000₽. Любое движение >0.01 pt = 10K₽. Не включать без фиксированного стопа.

---

## Состояние проекта

- ✅ PnL формула корректна во всех компонентах (broker, executor, lib_cvd_divergence, mtm_portfolio, scan_stop_hunt)
- ✅ PG portfolio: contracts=1 для всех enabled стратегий
- ✅ CH кластер: 2 реплики, ReplicatedMergeTree
- ✅ Stop Hunt scan: 36 tickers, Si/GZ/NG/LK/Eu/SR/RN — топ
- ❌ Common backtester (PortfolioEngine): CVD блокирует Stop Hunt на том же тикере. Использовать только для single-strategy тестов.
- ❌ Scan results с reinvest (dynamic sizing). Чистый 1-contract результат ниже.

## Файлы изменений

```
M CHANGELOG.md
M scripts/scan_stop_hunt.py          # PnL +lot*pct, PG хост
M strategies/common/broker.py        # _close_market +lot*pct
M strategies/common/executor.py      # contracts из PG, +pct
M strategies/cvd/prod/lib_cvd_divergence.py  # TICK_LOT, TICK_PCT
M strategies/cvd/scripts/mtm_portfolio.py    # sp/ms*lot(tkr)
A checkpoint/146-pnl-formula-fix.md
```
