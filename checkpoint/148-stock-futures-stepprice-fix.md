# 🔥 Checkpoint 148 — Stock Futures StepPrice Fix

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures
**Теги:** pnl-formula, stock-futures, stepprice-bug, ticker-specs

---

## Суть бага

MOEX хранит `STEPPRICE` по-разному для разных типов фьючерсов:

| Тип | Примеры | STEPPRICE | Нужен `*lot`? |
|-----|---------|-----------|:------------:|
| **Валютные** | Si, Eu, CR | ₽ за тик за **контракт** | ❌ |
| **Акционные** | GZ, RN, SR, VB | ₽ за тик за **штуку** | ✅ (lot=100) |
| **Товарные** | GD, NG | ₽ за тик за **контракт** | ❌ (lot=1) |

PG `futures.ticker_specs` хранил для всех `step_price=1.0`. Для акционных это означало:
- GZ: реальный 1 тик = 1₽ за штуку × 100 = **100₽** за контракт
- PG говорил 1₽. Формула без `*lot` давала 1₽ вместо 100₽ → **PnL занижен в 100×**

## Фикс

Умножил `step_price` на `lot_volume` для всех акционных фьючерсов в PG:

| Тикер | Было | Стало | lot | 
|-------|:----:|:-----:|:---:|
| GZ | 1.0 | **100.0** | 100 |
| RN | 1.0 | **100.0** | 100 |
| SR | 1.0 | **100.0** | 100 |
| VB | 1.0 | **100.0** | 100 |
| AL | 1.0 | **100.0** | 100 |
| LK | 1.0 | **10.0** | 10 |
| И др. | 1.0 | ×lot | — |

Теперь `step_price` для ВСЕХ тикеров — per-contract. Формула `(exit-entry)/ms*sp*pct - TC` без `*lot` — корректна для всех.

## Результаты (Stop Hunt, 5 tickers, 1 contract, 18 мес)

**Common backtester** (с broker, executor, equity tracking):

```
Капитал 100K:
  Equity: 250,894,791 ₽  (+250,794%)
  MDD:    18.05%
  WR:     66.0%  PF: 4.59  Trades: 10,410
  
  GZ: 112.5M, avg 47,186 ₽  ← 1pt=100₽, 472pt avg = 3.5% ✅
  RN: 129.7M, avg 60,142 ₽  ← 1pt=100₽, 601pt avg = 1.2% ✅
  Si:   5.2M, avg  2,298 ₽  ←  1pt=1₽, 2,298pt avg = 2.3% ✅
  GD:   3.1M, avg  1,627 ₽  ←  1pt=7.7₽, 211pt avg = 0.64% ✅
  CR:   0.4M, avg    210 ₽  ←  1pt=1₽, 210pt avg = 1.8% ✅
```

**bt_5t.py** (standalone, simpler logic):

```
PnL=173,825K WR=56.1% PF=2.40 Trades=11,051
```

## Файлы изменений

```
M scripts/bt_5t.py          # hosts 10.0.0.64→.60, CR asset_code, -lot из PnL
M checkpoint/148-stock-futures-stepprice-fix.md
M CHANGELOG.md
```

## Состояние (после чекпойнта 147)

- ✅ PnL формула: `(exit-entry)/ms*sp*pct - TC` без `*lot` — корректна для всех типов
- ✅ PG `futures.ticker_specs`: step_price per-contract для всех (акционные ×lot)
- ✅ `strategies/common/broker.py`: без `*lot`
- ✅ `strategies/common/executor.py`: Position без `lot`
- ✅ `strategies/common/paper_trader.py`: без `*lot`
- ✅ `strategies/cvd/prod/lib_cvd_divergence.py`: без `*lot`
- ✅ `strategies/cvd/scripts/mtm_portfolio.py`: без `*lot`
- ✅ `scripts/scan_stop_hunt.py`: без `*lot`, PG хост .60
- ✅ `scripts/bt_5t.py`: hosts .60, без `*lot`
