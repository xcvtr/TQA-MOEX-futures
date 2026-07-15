---
title: "Dragon на M1 — TZ fix, sweep 8 tickers, portfolio MM+GZ+GD"
checkpoint: 163
date: 2026-07-15
tags: [checkpoint, tqa-moex-futures, dragon, m1-bars, portfolio]
---

# Checkpoint 163: Dragon M1 — TZ fix + Portfolio

## Что сделано

### 1. TZ bug fix в M1 backtest'ах
Все M1 бэктесты использовали неверный часовой фильтр: `07:00-15:45 IRK` вместо `15:00-23:45 IRK`.
MOEX торгует 10:00-18:45 MSK = 15:00-23:45 IRK. Старый фильтр держал утренние часы (рынок закрыт)
и отрезал 90% сессии. Исправлено в 3 файлах:
- `strategies/dragon/scripts/backtest.py`
- `strategies/common/backtest.py`
- `strategies/dragon/scripts/sweep.py`

### 2. Sweep по 8 M1 тикерам (1 contract)
Данные MT5 FINAM, M5 detect + M1 tick, trailing TP/SL, комиссия 4₽

| Тикер | Сделок | WR | PnL | PF |
|:------|:------:|:--:|:---:|:--:|
| **MM** | 40 | **67.5%** | **+34,942₽** | **2.69** |
| **GD** | 73 | 60.3% | +5,890₽ | 1.35 |
| **GZ** | 61 | 57.4% | +283₽ | 1.13 |
| BR | 108 | 54.6% | +2,736₽ | 1.12 |
| CR | 14 | 42.9% | +18₽ | 1.28 |
| NG | 57 | 45.6% | -1,002₽ | 0.82 |
| RN | 49 | 46.9% | -1,618₽ | 0.76 |
| Si | 7 | — | — | — |

### 3. Портфельные бэктесты (200K капитал, КНУР×0.5)

**Вариант A: MM×2, GZ×2, GD×1** (GD×1 по ГО)
```
ПОРТФЕЛЬ (3 тикера, 174 сделок)
  Капитал: 200,000₽ → 276,340₽ (+38.17%)
  WR: 60.9% | PF: 2.23 | MDD: 5.90% | Calmar: 6.5
```

**Вариант B: MM×2, GZ×2** (без GD)
```
ПОРТФЕЛЬ (2 тикера, 101 сделок)
  Капитал: 200,000₽ → 270,450₽ (+35.23%)
  WR: 61.4% | PF: 2.55 | MDD: 5.68% | Calmar: 6.2
```

**Вариант C: MM×2, GZ×2, GD×2** (без ГО лимита)
```
ПОРТФЕЛЬ (3 тикера, 174 сделок)
  Капитал: 200,000₽ → 282,230₽ (+41.11%)
  WR: 60.9% | PF: 2.04 | MDD: 6.10% | Calmar: 6.7
```

## Основные выводы

1. **MM** — основной драйвер портфеля (67.5% WR, PF 2.69, низкое ГО=4,330₽)
2. **GD** — внятный PF 1.35, но ГО 83,885₽ — only 1 contract влезает
3. **GZ** — около нуля, MDD <1%, можно держать как diversifier
4. **NG** — слабый (данных всего с фев 2026), не играет

По сравнению с checkpoint 161 (M5 tradestats_fo): там были миллионы, тут десятки тысяч.
Разница из-за другого data vendor (tradestats_fo vs MT5 FINAM), таймфрейма (M5 vs M1 tick),
и вероятно TZ-бага в оригинале.

## Новые файлы

| Файл | Описание |
|:-----|:---------|
| `strategies/dragon/scripts/portfolio_test.py` | Портфельный бэктест с MTM DD, GO check, reinvest |
| `strategies/dragon/scripts/sweep_m1.py` | Sweep по M1 данным |

## Изменённые файлы

| Файл | Изменение |
|:-----|:----------|
| `strategies/dragon/scripts/backtest.py` | TZ fix: 07:00→15:00 IRK |
| `strategies/common/backtest.py` | TZ fix: 07:00→15:00 IRK |
| `strategies/dragon/scripts/sweep.py` | TZ fix: 07:00→15:00 IRK |

## Параметры Dragon M1

| Параметр | Значение |
|:---------|:--------:|
| Data | CH moex.mt5_bars (MT5 FINAM M1) |
| Detect interval | M5 (каждые 5 M1 баров) |
| Tick | M1 (каждый бар) |
| Commision | 4₽ round-trip |
| Trailing TP activation | 0.5% |
| Trailing TP trail | 0.3% |
| Timeout | 60 M1 bars (60 min) |
| Stop loss | 0.7% (hard SL) |
| Capital | 200,000₽ |
| Portfolio | MM×2, GZ×2, GD×1 |
