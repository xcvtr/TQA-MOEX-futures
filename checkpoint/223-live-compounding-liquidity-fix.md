---
title: "Реинвест/компаунд в live: лимиты от реальных объёмов AlgoPack (BR 75K, NG 159K, SV 85K лотов)"
checkpoint: 223
date: 2026-08-09
tags: [checkpoint, moex, futures, live, compounding, reinvest, liquidity, paper-trader]
---

# Checkpoint 223 — Реинвест/компаунд в live: лимиты исправлены

## Контекст

Пользователь: «нам надо преодолеть компаунд и реинвест (добиться их в реале)».
Цель: чтобы лоты росли от фактического капитала в live, как в бэктесте — а не
упирались в искусственные лимиты.

## 🔍 Проблема: реинвест был, но 3 лимита душили компаунд

Реинвест в коде УЖЕ был:
- `equity += c['pnl']` (paper_trader.py:840) — капитал растёт после каждой сделки
- `contracts = max(1, int(equity * risk / go))` (paper_trader.py:901) — лоты от equity

НО три лимита не давали лотам расти:

| # | Лимит | Код | Эффект |
|---|:------|:----|:-------|
| 1 | **Volume cap** | `contracts = min(contracts, int(b_vol * 0.2))` | b_vol = tick_volume (55 для BR) → **макс 11 лотов**! Компаунд умирал на ~5M |
| 2 | **TICKER_LIMITS** | `BR: 100, NG: 100, SV: 80` | потолок 80-100 лотов |
| 3 | **MAX_CONTRACTS** | `= 20` | запасной потолок |

Корень: `b_vol` из `mt5_continuous.vol` — это **tick_volume** (число сделок),
а не контракты. 20% от tick_volume = мизер.

## 🔧 Фикс в `strategies/common/paper_trader.py`

1. **`load_daily_volumes()`** — при старте грузит реальные дневные объёмы
   (контракты/день) из AlgoPack `moex.tradestats_fo` за 400 дней:
   - BR 750 837, NG 1 589 780, SV(SILV) 846 148 контрактов/день
   - Перпетуалы (USDRUBF и др.) не покрыты AlgoPack — оценка mt5 tick×1440×20
   - Маппинг AlgoPack SILV→SV, GOLD→GD

2. **Volume cap** теперь: `DAILY_VOL[ticker] × LIQ_FRAC` (10% дневного объёма =
   ёмкость рынка), fallback на старый b_vol×vc если объём не загрузился.

3. **TICKER_LIMITS** обновляются при старте: BR 75083, NG 158978, SV 84614 лотов
   (было 100/100/80). Компаунд теперь не упирается до ~100M+.

4. **MAX_CONTRACTS** 20 → 1000.

## ✅ Проверено

- Компиляция OK
- `load_daily_volumes()`: BR=75083, NG=158978, SV=84614 (DAILY_VOL мутируется,
  imported references валидны)
- Live-трейдер запускается: `Eq=200000₽ Открыто=0 Сделок=0` — чисто
- Git: `a275f69` (до чекпойнта)

## 🔧 PG ticker_specs — КСУР-ГО доведён (обновление после чекпойнта)

Проверка «ксур учтено?» выявила пробел: перпетуалы НЕ были в PG `ticker_specs`
(базис-арбитраж в live не нашёл бы ГО → go=0 → крах sizing), а Eu имел СТАРОЕ
ГО без КСУР (39714 вместо 3835 → в 10× меньше лотов).

| Тикер | ГО | Статус |
|:------|-----:|:-------|
| BR | 26903 | ✅ было (нет ПГО) |
| NG | 3900 | ✅ было |
| SV | 5983 | ✅ было |
| Si | 2676 | ✅ было |
| Eu | **3835** (было 39714) | 🔧 исправлен |
| USDRUBF | **2633** | ➕ добавлен |
| EURRUBF | **4493** | ➕ добавлен |
| CNYRUBF | **128** | ➕ добавлен |

Вставлено: `INSERT ... ON CONFLICT (ticker) DO UPDATE`, Eu — `UPDATE`.
Все бэктесты (portfolio_test hardcode 5309/8328/302, oi_reopt specs_from_pg) —
согласованы с PG.

## 📌 Состояние для продолжения

1. **Paper trader начнёт реинвестировать с понедельника 10.08** (новые лимиты активны)
2. **Это всё ещё paper** — мост к MT5 (FINAM) / Finam API НЕ написан, реальные деньги
   не двигаются. Мост: `order_send` + iceberg 20-50 лотов + синхронизация fills (паттерн
   TQA-FX-TOP `mt5_bridge.py`)
3. Символы исполнения: OI = квартальные `BRQ6`/`NGQ6`/`SVQ6` (не ALLFUT — indicative);
   базис = `Si`, `USDRUBF`, `Eu`, `EURRUBF`, `CNY`, `CNYRUBF`
4. Риски при росте капитала: slippage при исполнении 100+ лотов сериями,
   мониторинг маржи (лимит 80% equity уже есть)

## Файлы

- `strategies/common/paper_trader.py` — load_daily_volumes() + новые лимиты
- Предыдущий: `checkpoint/222-basis-arbitrage-ksur.md` — базис-арбитраж (портфель
  OI+BA: risk=8% pyr=1 lots≤1000 → CAGR 1550%, MTM 13%, Calmar 119, после аудита)
