---
title: "Dragon FINAL — корреляционный портфель NG/SV/BR/MM, +227%, MDD 13%"
checkpoint: 165
date: 2026-07-16
tags: [checkpoint, tqa-moex-futures, dragon, mt5-continuous, portfolio-final]
---

# Checkpoint 165: Dragon FINAL — Correlation-Optimized Portfolio

## Итог работы

### Источник данных
MT5 FINAM (account 10187 FINAM-AO), **Indicative Continuous** раздел.
10.5M M1 баров в `moex.mt5_continuous` по 9 тикерам с 2020 года.

### Sweep 113 continuous
Все 113 indicative continuous символов FINAM MT5 протестированы.
33 имеют PG specs (ГО, min_step, step_price).
**8 показали edge** (PF>1.2): BR, NG, RN, SV, MM, Si, CR, GZ.

### Grid search параметров
100 комбинаций SL/trail_act/trail_trail.
**Оптимум: sl=1%, trail_act=1.5%, trail_trail=0.5%**

### Корреляционный анализ
Ключевые находки:
- NG↔SV: **-0.03** (не коррелируют) → идеальная пара
- NG↔MM: **0.04** (не коррелируют)
- BR↔MM: **-0.01** (не коррелируют)
- RN↔GZ: **0.70** (дублируют) → убраны
- CR↔Si: **0.97** (почти одно и то же)

### Финальный портфель

**4 тикера:** NG (30%), SV (25%), BR (20%), MM (25%)

| Параметр | Значение |
|:---------|:---------|
| Депозит | 200,000₽ |
| KNUR | 0.5 |
| ГО | биржевое × 0.5 (услуга пониженного ГО) |
| SL | 1% |
| Trail activation | 1.5% |
| Trail trail | 0.5% |
| Комиссия | 4₽ round-trip |
| Риск | **7%** от капитала тикера |
| Реинвест | да |

**Результат: 200K → 655K (+227%), MDD 13.1%, PF 1.66, Calmar 17.4**

Альтернатива (макс доходность при MDD<20%):
- Risk 15%: **+262%, MDD 18%**, PF 2.13

### Скрипты
| Файл | Описание |
|:-----|:---------|
| `strategies/dragon/scripts/custom_alloc_sweep.py` | Финальный — alloc, GO, reinvest |
| `strategies/dragon/scripts/pull_and_test_all.py` | Pull+test всех PG тикеров из MT5 |
| `strategies/dragon/scripts/final_v2.py` | Предфинальная версия |
| `strategies/dragon/scripts/time_aligned_portfolio.py` | Time-aligned (честный) |
| `strategies/dragon/scripts/risk_sweep.py` | Sweep по risk уровням |
