---
title: "Stop Hunt COMBINED — SHORT+LONG, 5 tickers, 56.4% WR, 2.03 PF"
checkpoint: 141
date: 2026-07-04
tags: [checkpoint, tqa-moex-futures, backtest, stop-hunt]
---

# Checkpoint 141 — Stop Hunt COMBINED (SHORT+LONG)

**Дата:** 2026-07-04

## Финальный backtest — Stop Hunt COMBINED

**Параметры:** 1 contract, TO=12 (60 min), trailing TP 0.5/0.3, slip 1 tick, comm=4 RUB, open[i+1] entry, lo_hist[20], hi_hist[20], **both SHORT and LONG directions**. Jan'25 — Jun'26, `tradestats_fo`.

```
Total PnL:  +7,303K    WR: 56.4%    PF: 2.03
Trades:      9,109
Avg W:      +2,801     Avg L: -1,788
```

| Direction | Trades | PnL | WR | PF |
|:---------:|:-----:|----:|:--:|:--:|
| **LONG**  | 6,083 | +6,144K | **60.5%** | **2.22** |
| SHORT     | 3,026 | +1,159K | 48.3% | 1.57 |

### Per ticker

| Ticker | Lot | Trades | PnL | WR | % of PnL |
|--------|:---:|:------:|----:|:--:|:--------:|
| **GD** (GOLD) | 1 | 2,114 | **+2,957K** | **59.3%** | **40%** |
| **Si** | 1000 | 2,386 | +2,687K | 53.9% | 37% |
| **RN** (ROSN) | 100 | 2,157 | +1,106K | **58.3%** | 15% |
| GZ (GAZR) | 100 | 2,452 | +554K | 54.9% | 8% |
| CR (CNYRUBF) | — | 0 | — | — | нет данных |

### По сравнению с 4 тикерами (chkpt 137)

| Метрика | 4 ticker (SH only) | **5 ticker (SH+LONG)** |
|---|---|---|
| Trades | 7,785 | **9,109** |
| PnL | +3,618K | **+7,303K** |
| WR | 53.5% | **56.4%** |
| PF | 1.75 | **2.03** |

Добавление LONG (лонговой версии стоп-ханта + bottom fishing) и RN/GD дало **x2 PnL** и рост WR/PF.

## Портфель текущий

| Ticker | Asset | Strategies | WR |
|---|---|---|---|
| **GD** | GOLD | stop_hunt + cvd | **59.3%** |
| **RN** | ROSN | stop_hunt + cvd | **58.3%** |
| **GZ** | GAZR | stop_hunt + cvd | 54.9% |
| **Si** | Si | stop_hunt + cvd | 53.9% |
| **CR** | CNYRUBF | stop_hunt + cvd | нет данных |
| NG | NG | 🔴 disabled | |
| W4, VB, SR | | 🔴 disabled | |

## Paper trader

- Cron: каждые 5 мин, 10:00-18:00, пн-пт
- Старт в понедельник 10:00
- PG config: `futures.portfolio`
- State: `futures.paper_state`
- Старые позиции от CVD-divergence: сброшены

## Известные баги (paper_trader.py)

1. Entry по prc_prev (лаг 10 мин)
2. dcvd_z = 0 (CVD мёртв)
3. timeout по len(df) не по времени
4. Нет lot → PnL=0 если в specs нет lot

Патчить после старта в понедельник.
