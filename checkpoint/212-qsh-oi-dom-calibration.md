---
title: "oi_dom калиброван: акции работают, сырьё нет. QScalp импортирован"
checkpoint: 212
date: 2026-08-02
tags: [checkpoint, oi-dom, qsh, qscalp, стакан]
---

# Checkpoint 212: QScalp импорт + oi_dom калибровка

## 🏆 Ключевые выводы

**Подтверждение стаканом работает для ФЬЮЧЕРСОВ НА АКЦИИ, не для сырья:**
- TATN (фьючерс на акции): WR 54.6% → **67.4%** (с подтверждением) — совпадает с PG снапшотами (67%)
- RN (ROSN, фьючерс на акции): WR 61% (PG стакан)
- BR/NG/SV (сырьё): WR ~53% (стакан НЕ помогает)

**Портфель (2026, 7 мес):**
| | Без стакана | С стаканом (RN/TATN) |
|:--|:-----------:|:--------------------:|
| ROI | +463% | +414% (-11%) |
| MTM DD | 45.4% | **20.9% (-54%)** |
| Calmar | 10.2 | **19.8 (×2)** |

**Risk 50% равномерно — оптимум** (Calmar 21, ROI +464%, DD 21.6%). Per-symbol риск хуже.

## 📦 QScalp архив импортирован (SMB \\10.0.122.2\qsh)

| Таблица | Данные |
|:--------|:-------|
| moex.dom_qsh | стакан (дельты) 208 дней, **1.286 млрд строк** |
| moex.deals_qsh | сделки (агрессор ASK/BID) 201 день, 27.8M |
| moex.dom_imb_qsh | минутный imbalance (ближние уровни от спреда), 43 контракта |

**Формат qsh:** gzip → QSHParser (LEB128, tools/qsh_parser-master). Кадры Quotes = инкрементальные дельты + полные снапшоты (>30 уровней). Deals = trade_type ASK(покупка)/BID(продажа).

**TZ:** MSK (подтверждено: qsh NGQ6 2.753 = mt5 NG 2.751).

**Питфоллы:**
1. QSHParser даёт битые кадры (год 1) → фильтр `year < 2000`
2. pkill убивает обёртку, не python → `pkill -f` + `setsid`
3. Полные кадры ≠ дельты: imbalance только по полным (>30 уровней)
4. mid = (best_bid+best_ask)/2 (не медиана уровней!)
5. data/qsh тестовые = синтетика (воскресные часы)

## 🧠 Метод oi_dom (подтверждение)

```
OI: физ продают (contrarian) → long
+ Стакан: ask-heavy (imbalance > 0.1, ближние уровни от спреда) → ПОДТВЕРЖДЕНИЕ
long → ask-heavy, short → bid-heavy (НАПРАВЛЕННО!)
```

## 🔧 Live (2 стратегии, 5 тикеров)

| Тикер | Стратегия | Стакан |
|:------|:----------|:-------|
| BR/NG/SV | oi | нет (не помогает) |
| **RN** | oi_dom | PG futures.dom (WR 61%) |
| **TATN** | oi_dom | qsh dom_imb_qsh (WR 67.4%) |

## 📁 Файлы
- `scripts/qsh_import_all.py` — импорт Quotes (--tickers)
- `scripts/qsh_deals_import.py` — импорт Deals
- `scripts/qsh_imb_materialize.py` — минутный imbalance (ближние уровни)
- `scripts/qsh_to_ch.py` — один день
- `strategies/oi_dom/prod/engine.py` — OI + подтверждение

## Состояние для продолжения
- Дальнейшие улучшения: свип hold, thr per-ticker, deals-подтверждение (агрессор)
- TATN/RN oi_dom в live (risk 40-50%)
- Сырьё (BR/NG/SV): oi без стакана
