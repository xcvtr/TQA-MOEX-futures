---
title: "OI-стратегия честная: BR+NG+SV, +1,042%, MTM DD 20.9%"
checkpoint: 205
date: 2026-08-02
tags: [checkpoint, oi, futoi, timezone-fix, guard-fix]
---

# Checkpoint 205: OI-стратегия — ФИНАЛЬНЫЕ честные цифры

## 🏆 Результат (после 2 критических багов)

**BR+NG+SV, thr=-3%, hold 60 мин, risk 15%, slip 2 тика**
- **ROI +1,042%** за 2.5 года (~280%/год)
- **MTM DD 20.9%** (unrealized учтён)
- Сделок: 2,164
- Капитал: 200,000₽ → ~2,284,000₽

## 🚨 2 КРИТИЧЕСКИХ БАГА НАЙДЕНЫ И ИСПРАВЛЕНЫ

### Баг 1: Таймзона futoi (MSK) vs mt5_continuous (IRK)
- futoi bt = **MSK** (loader пишет ISS tradedate/tradetime без конвертации)
- mt5_continuous bt = **IRK** (fromtimestamp на хосте Asia/Irkutsk +8)
- Бэктест матчил напрямую → вход на 5ч РАНЬШЕ сигнала = **look-ahead**
- Фикс: `irk = bt_futoi + timedelta(hours=5)`
- Доказательства: unix-якорь MT5 (бар unix=1785483480 → bt=15:38 IRK), суббота (вечер пятницы в 00-07 IRK), провал 8-14 IRK = 03:00-09:59 MSK закрыто

### Баг 2: Guard CLOSE чужих позиций
- События OPEN/CLOSE сортировались; CLOSE от ПРОПУЩЕННОГО сигнала закрывал реальную позицию
- → 5-минутные сделки вместо 60, 10K сделок вместо 2K
- Симптом: сделка 17:00→17:05 IRK (hold 5 мин)
- Фикс: последовательный проход, пропуск сигнала БЕЗ создания CLOSE

### Баг 3 (ранее): Backfill +8ч вместо MSK
- Докачка май-июль писала bt=MSK+8 (неверно, надо MSK как loader)
- Перекачано: bt = MSK без конвертации

## 📊 Эволюция цифр (все ложные → честные)

| Версия | ROI | Проблема |
|:-------|:---:|:---------|
| Первый (look-ahead 5ч) | +5,905% | таймзона |
| После фикса таймзоны | +536% | guard CLOSE |
| Свип без guard | +9,818% | guard |
| **ЧЕСТНО** | **+1,042%** | ✅ |

## 📊 Свип по 64 тикерам (честный, 1 контракт, thr -3/-5/-7/-10)

Рабочие: **BR** (thr-3: WR 72.7%, +2,326K), **NG** (thr-3: WR 69.1%, +1,599K), **SV** (thr-3: WR 66.1%, +516K), RN (thr-3: WR 59.3%, +304K)
Артефакты: **ED** (WR 97-100% — цена 1.13-1.17, мусор), X5 (WR 9-19%), SR/SBRF (WR 31%)
Убыточны: GD, MM, CR, GZ, MG, SP/SBPR

## 📊 Портфельные конфигурации (MTM DD)

| Risk | Hold | ROI | MTM DD | Сделок |
|:----:|:----:|:---:|:------:|:------:|
| 10% | 60м | +368% | 14.0% | 2,164 |
| 10% | 120м | +321% | 20.6% | 1,261 |
| **15%** | **60м** | **+1,042%** | **20.9%** | 2,164 |

## ✅ Данные (полные)

- 64 тикера futoi, полные до 18.07.2026 (докачано 755K записей май-июль)
- Пробел 19-28.07 — 14-дневное окно ISS, докачаем 16.08
- Скрипт: `scripts/oi/backfill_futoi_iss.py` (по одной дате, MSK-шкала)

## 🔧 В live (обновлено)

- PG portfolio: BR/NG/SV, params {"thr":-3.0,"risk":0.15}, timeout_bars=60, trailing/SL=0.99
- Paper trader: strategies/common/paper_trader.py + strategies/oi/prod/engine.py
- Крон: */5 15-23,0-4 → run_moex_paper_trader.sh
- State сброшен

## 📁 Файлы
- `scripts/oi/backfill_futoi_iss.py` — докачка futoi (ISS, по датам)
- `scripts/oi/oi_sweep_honest.py` — честный свип 64 тикеров
- `strategies/oi/prod/engine.py` — OI плагин
- `strategies/common/paper_trader.py` — фреймворк (fetch_day_net, oi в STRATEGY_MAP)

## Состояние для продолжения
- OI live готов (старт пн 4.08)
- 16.08: докачать 19-28 июля
- Возможности: RN (слабее), субботняя сессия (18-23 IRK), hold 60 vs 30
