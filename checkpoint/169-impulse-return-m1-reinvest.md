---
title: "Impulse Return — +263% годовых на M1, реинвест 1%"
checkpoint: 169
date: 2026-07-22
tags: [checkpoint, tqa-moex-futures, impulse-return, m1, reinvest]
---

# Checkpoint 169: Impulse Return — прорыв на M1

## Результаты

**Impulse Return** отдельно, реинвест 1%, 365 дней, M1 бары из `moex.mt5_bars`:

| Тикер | Сделок | WR | PnL | PF |
|:------|:-----:|:--:|----:|:--:|
| **BR** | 334 | 60.8% | **+200,188** | 1.89 |
| **RN** | 155 | 59.4% | **+153,163** | 2.24 |
| **GD** | 189 | 55.0% | **+69,771** | 1.67 |
| **GZ** | 157 | 56.1% | **+64,271** | 1.63 |
| **Si** | 33 | 54.5% | **+21,107** | 2.41 |
| **CR** | 41 | 56.1% | **+18,026** | 1.80 |
| **ИТОГО** | **909** | **~57%** | **+526,526 (+263%)** | **1.83** |

**Капитал: 200K → 726K** (+263% годовых, MDD не считан)

### Параметры IR
- `impulse_bars=12` (адаптировано под M1)
- `impulse_pct=0.5`, `retrace=0.618`
- Trailing TP: 0.5%/0.3%, timeout=12 bars, SL=0.7%
- Реинвест: 1% риска на сделку

### Dragon (отдельно)
| Тикер | Сделок | WR | PnL | PF |
|:------|:-----:|:--:|----:|:--:|
| GZ | 64 | 50% | +15,442 | 1.40 |
| BR | 121 | 47.1% | +6,393 | 1.08 |
| GD | 81 | 55.6% | +3,074 | 1.07 |
| CR | 10 | 50% | +2,142 | 1.23 |
| RN | 57 | 35.1% | -22,238 | 0.47 |

**Stop Hunt: 0 сделок — отключён**

## Состояние PG portfolio

| Стратегия | Тикеры | Статус |
|:----------|:-------|:-------|
| **Impulse Return** | BR, CR, GD, GZ, RN, Si | ✅ active, contracts=NULL (reinvest) |
| **Dragon** | GZ(×2), MM(×2), NG(×2), BR, CR, GD, Si, SV | ✅ active, contracts=1/2 |
| **Stop Hunt** | — | ❌ отключена (0 сделок) |

## Изменения в коде
- `strategies/common/backtest.py` — добавлен `--reinvest`, M1 адаптация
- `strategies/stop_hunt/prod/engine.py` — lookback 20→60 (M1)
- `strategies/impulse_return/prod/engine.py` — impulse_bars 4→12 (M1)
