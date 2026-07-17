---
title: "IR FINAL — audit, fixes, slippage test, FINAM vs AlgoPack"
checkpoint: 167
date: 2026-07-17
tags: [checkpoint, tqa-moex-futures, ir, impulse-return, final]
---

# Checkpoint 167: IR — Audit & Final Results

## Аудит (4 бага найдено и исправлено)

| Баг | Описание | Фикс |
|:----|:---------|:------|
| **Cooldown** | `reset_state()` — pass. После закрытия сигнал на следующем баре | `_cooldown_state[ticker] = cooldown (24 бара)` |
| **Объёмный фильтр** | `median_vol` никогда не передавался, фильтр всегда True | `np.median(full_vol_hist)` из истории объёмов |
| **min_vol** | Вход при нулевом объёме | `vol <= 0 → return None` |
| **Slippage** | 1 tick hardcoded | `slippage_in` параметр в BrokerSim/PortfolioEngine |

## Slippage audit (IR, 5 tickers, 1 contract)

| Slippage | Доходность | MTM MDD | PF |
|:--------:|:----------:|:-------:|:--:|
| 2 tick | **+3,343%** | 2.55% | 10.42 |
| 6 tick | +3,321% | 2.55% | 10.21 |
| 10 tick | +3,292% | 2.56% | 9.96 |
| 20 tick | +2,874% | 2.63% | 6.67 |

## FINAM MT5 vs AlgoPack (tradestats_fo)

| Тикер | tradestats_fo PF | MT5 FINAM PF |
|:------|:----------------:|:------------:|
| CR | 5.61 | 0.43 ❌ |
| GD | 5.58 | 0.83 ❌ |
| GZ | 7.33 | 0.57 ❌ |
| RN | 6.99 | 1.21 ❌ |
| Si | 6.03 | 1.75 ❌ |

Edge не переносится между data vendors. Live использует tradestats_fo (source #3 в paper_trader.py).

## Финальные результаты (IR, after fixes, tradestats_fo)

**200K → 2.1M (+1,057%), MTM MDD 1.24%, PF 9.66, 2,497 trades**

## Изменённые файлы

| Файл | Изменение |
|:-----|:----------|
| `strategies/impulse_return/prod/engine.py` | Cooldown, vol filter, min_vol, reset_state |
| `strategies/common/engine.py` | `slippage_in` parameter |
| `strategies/common/broker.py` | `slippage_in` parameter |
| `strategies/common/executor.py` | Fixed ms/sp field names, GO check |
