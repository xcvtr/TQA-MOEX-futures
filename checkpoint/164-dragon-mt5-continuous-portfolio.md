---
title: "Dragon на MT5 Continuous — sweep 113, grid search, portfolio alloc+GO+KNUR"
checkpoint: 164
date: 2026-07-16
tags: [checkpoint, tqa-moex-futures, dragon, mt5-continuous, portfolio]
---

# Checkpoint 164: Dragon MT5 Continuous — Full Pipeline

## Что сделано

### 1. MT5 Continuous данные (Indicative Continuous)
Найден раздел **Indicative Continuous** в MT5 FINAM (113 символов).
Данные стянуты в CH `moex.mt5_continuous` для 9 тикеров (2020→now, 10.5M M1 баров):
- BR, Si, CR, GD, GZ, MM, NG, RN, SV

### 2. Sweep всех 113 continuous
Каждый символ протестирован: 1 год M1, MT5 specs (ms, sp из trade_tick_value).
**67 символов с PF>1, MDD<20%.** Лучшие: ALLFUTBR, ALLFUTROSN, ALLFUTSILV, ALLFUTGOLD.

### 3. Grid search (SL/trail_act/trail_trail)
100 комбинаций на BR+NG+RN+MM. **Оптимум: sl=1%, trail_act=1.5%, trail_trail=0.5%**
- PnL +370K (+185%), MDD 14%, PF 1.60

### 4. Time-aligned portfolio (честный)
Все тикеры одновременно, единый капитал. Без «резинового» sequential.

### 5. Portfolio с ограничениями
- Per-ticker allocation (равный)
- Reinvest (риск % от аллока тикера)
- GO check с KNUR
- Комиссия TC=4₽

## Финальные результаты

**Параметры:** депо 200K, SL=1%, trail_act=1.5%, trail_trail=0.5%, KNUR=0.7

| Риск | Капитал | Доходность | MDD | PF | Calmar |
|:----:|:-------:|:----------:|:---:|:--:|:------:|
| 3% 🏆 | 362K | **+81%** | **4.0%** | **1.76** | **20.3** |
| 4% | 342K | +71% | 6.3% | 1.47 | 11.3 |
| 5% | 338K | +69% | 7.4% | 1.39 | 9.3 |

По тикерам (risk 3%):
| Тикер | Сделок | Ср.конт | PnL | PF |
|:------|:------:|:-------:|:---:|:--:|
| NG | 135 | 4.1 | **+90K** | 3.53 |
| SV | 204 | 1.4 | +36K | 1.82 |
| RN | 310 | 2.1 | +16K | 1.23 |
| GZ | 167 | 5.7 | +7K | 1.26 |
| CR | 57 | 6.0 | +2K | 1.58 |
| BR | 148 | 1.0 | +3K | 1.17 |
| MM | 100 | 1.0 | +4K | 1.40 |
| Si | 62 | 1.0 | +3K | 1.84 |

## Скрипты

| Файл | Назначение |
|:-----|:-----------|
| `scripts/pull_continuous_m1.py` | Pull MT5 indicative continuous → CH |
| `strategies/dragon/scripts/sweep_113.py` | Sweep всех 113 continuous |
| `strategies/dragon/scripts/grid_continuous.py` | Grid search params |
| `strategies/dragon/scripts/time_aligned_portfolio.py` | Time-aligned портфель |
| `strategies/dragon/scripts/portfolio_alloc.py` | Per-ticker alloc |
| `strategies/dragon/scripts/final_v2.py` | Финальный: alloc+GO+KNUR+reinvest |
| `scripts/load_mt5_bars.py` | MT5 loader (FINAM path, dynamic contracts) |
| `scripts/populate_continuous.py` | Заполнение _best_secid, ticker_mapping |

## Данные

| Таблица | Описание | Строк |
|:--------|:---------|:-----:|
| `moex.mt5_continuous` | MT5 continuous M1 (2020→now) | 10.5M |
| `moex._best_secid` | Активные контракты по объёму | 5 |
| `moex._daily_best_secid` | Дневные активные контракты | 7.9K |
| `moex.ticker_mapping` | Маппинг префикс→тикер | 110 |
