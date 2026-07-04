---
title: "Portfolio scan — best 60 tickers, RN/GD added"
checkpoint: 139
date: 2026-07-04
tags: [checkpoint, tqa-moex-futures, scan, portfolio]
---

# Checkpoint 139 — Portfolio scan, RN/GD added

## Scan results (60 tickers, Stop Hunt)
 
**Filter:** TO=12, 1 contract, WR≥35%, trades≥150

### Top 10 by Sharpe

| # | Ticker | PnL | WR | PF | Sharpe |
|---|---|---|---|---|---|
| 1 | RN (ROSN) | +1,172K | 58.2% | 2.87 | 33.82 |
| 2 | MM (MXI) | +899K | 53.4% | 2.45 | 31.42 |
| 3 | GZ (GAZR) | +638K | 55.4% | 1.90 | 28.15 |
| 4 | Si | +2,988K | 53.9% | 1.79 | 25.39 |
| 5 | GD (GOLD) | +3,606K | 59.9% | 2.32 | 24.66 |
| 6 | SV (SILV) | +847K | 59.3% | 1.88 | 22.22 |
| 7 | CR (CNY) | +220K | 51.4% | 1.52 | 20.80 |
| 8 | Eu | +1,231K | 52.2% | 1.63 | 19.66 |
| 9 | SN (SNGR) | +156K | 53.6% | 1.61 | 15.01 |
| 10 | MG (MAGN) | +181K | 54.2% | 1.62 | 14.14 |

## Новый портфель

| Ticker | Strategies | Status |
|---|---|---|
| Si | stop_hunt + cvd | ✅ |
| GZ | stop_hunt + cvd | ✅ |
| **RN** | **stop_hunt + cvd** | 🆕 |
| **GD** | **stop_hunt + cvd** | 🆕 |
| CR | stop_hunt + cvd | ✅ (без данных) |
| NG | stop_hunt | ❌ disabled |
| W4, VB, SR | — | ❌ disabled |

## Paper trader

- Cron: каждые 5 мин, 10:00-18:00 МСК, пн-пт
- Портфель: PG `futures.portfolio` (enabled=true)
- Stop Hunt: 53.5% WR, 1.75 PF (TO=12)
- Состояние: PG `futures.paper_state`
- Сделки: PG `futures.paper_trades`

## Известные баги

1. **Entry по prc_prev (10 мин лаг)** — paper_trader.py:373. signal → entry по bar[-2] close
2. **CVD мёртв** — dcvd_z=0, paper_trader.py:321
3. **bar_idx = len(df)** — не глобальный счётчик, timeout может не работать
4. **no lot check** — если в specs нет lot → PnL=0
