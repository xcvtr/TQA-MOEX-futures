---
title: "Dragon + IR + TRIZ = ~600% годовых, MDD ≤ 20%"
checkpoint: 172
date: 2026-07-22
tags: [checkpoint, tqa-moex-futures, dragon, impulse-return, triz]
---

# Checkpoint 172: Финальный портфель — Dragon + IR + TRIZ

## Состав портфеля
| Стратегия | Тикеры | Риск | Detect |
|:----------|:-------|:----:|:------|
| **Dragon** 🐉 | MM (3-min), GZ (5-min) | 2% | resample → M1 |
| **Impulse Return** ⚡ | **Si** | 1% | M1 |

## Результаты (365д, common pool, trend filter)

| Стратегия | Тикер | ROI | WR | PF | MDD |
|:----------|:------|:---:|:--:|:--:|:---:|
| **Dragon** 🐉 | MM | **+363%** | 51.0% | **2.10** | **19.7%** |
| **Dragon** 🐉 | GZ | +156% | 53.0% | 1.66 | 13.0% |
| **IR** ⚡ | **Si** | **+669%** | **61.5%** | **3.19** | **9.6%** |
| **ПОРТФЕЛЬ** | — | **~600%** | **~55%** | **~2.0** | **~20%** |

## TRIZ-улучшения
1. **Trend filter** — SMA 50 на M1, сделки только по тренду. PF +50%
2. **Common pool** — весь капитал на каждый сигнал (vs per-symbol)
3. **Per-symbol detect** — свой таймфрейм для каждого тикера
4. **Multi-strategy** — Dragon + IR с приоритетом по score

## История
| № | Результат |
|:-:|:----------|
| 172 | **Dragon + IR + TRIZ = ~600%** 🏆 |
| 171 | Dragon + TRIZ common pool = +519% |
| 170 | Dragon 5-min detect + M1 tick = +412% |
| 169 | Impulse Return +263% отдельно |
| 150 | Старый портфель +405% (исторический) |
