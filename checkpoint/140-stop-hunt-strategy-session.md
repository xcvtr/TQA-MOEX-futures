---
title: "Stop Hunt strategy — full session summary"
checkpoint: 140
date: 2026-07-04
tags: [checkpoint, tqa-moex-futures, stop-hunt, paper-trader]
---

# Checkpoint 140 — Stop Hunt strategy

## Что за стратегия

**Stop Hunt** (ложный пробой). Python-порт Excavator MQL5.

### Механика

```
1. Ищем бары с длинными тенями
2. Цена пробивает уровень (hi[-1] > max(hi[-3:-1]) или lo[-1] < min(lo[-3:-1]))
3. И сразу возвращается обратно (close внутри диапазона)
4. = стоп-хант: маркет-мейкер выбил стопы
5. Входим ПРОТИВ пробоя: long после false short, short после false long
6. Выход: trailing TP (0.5% activation, 0.3% trail, timeout 12 bars)
```

### Код

`strategies/stop_hunt/prod/engine.py` — `check_signal(bar_data, ticker)`.

Сигнал: `hi[-1] > max(hi[-3:-1])` и `close < hi[-1]` → short. Аналогично для long.

## Scan results (60 tickers)

Stop Hunt, TO=12, 1 contract, 18 мес.

### Топ по Sharpe (WR>50%)

| # | Ticker | PnL | WR | PF | Sharpe |
|---|---|---|---|---|---|
| 1 | **RN** (Роснефть) | +1,172K | 58.2% | 2.87 | 33.82 |
| 2 | **MM** (MXI) | +899K | 53.4% | 2.45 | 31.42 |
| 3 | **GZ** (Газпром) | +638K | 55.4% | 1.90 | 28.15 |
| 4 | **Si** (USDRUB) | +2,988K | 53.9% | 1.79 | 25.39 |
| 5 | **GD** (GOLD) | +3,606K | 59.9% | 2.32 | 24.66 |
| 6 | **SV** (SILV) | +847K | 59.3% | 1.88 | 22.22 |
| 7 | **CR** (CNYRUB) | +220K | 51.4% | 1.52 | 20.80 |

### Худшие

| Ticker | PnL | WR | PF |
|---|---|---|---|
| BR (Brent) | -254K | 49.5% | 0.76 |
| CC (Cocoa) | -49K | 46.6% | 0.53 |
| BM (BRM) | -38K | 45.4% | 0.50 |
| MC (MTLR) | -37K | 52.4% | 0.71 |

## Текущий портфель (PG futures.portfolio)

| Ticker | Strategies | Status |
|---|---|---|
| **GZ** (GAZR) | stop_hunt + cvd | ✅ |
| **Si** | stop_hunt + cvd | ✅ |
| **RN** (ROSN) | stop_hunt + cvd | 🆕 |
| **GD** (GOLD) | stop_hunt + cvd | 🆕 |
| CR (CNYRUBF) | stop_hunt + cvd | ✅ wait data |
| NG, W4, VB, SR | — | ❌ disabled |

## Paper trader bugs (не чинили)

1. **Entry по prc_prev (10 мин лаг)** — paper_trader.py:373
2. **CVD мёртв** — dcvd_z=0, paper_trader.py:321
3. **bar_idx = len(df)** — не глобальный, timeout может не сработать
4. **no lot check** — если specs нет lot → PnL=0

## Cron

- `Stop Hunt paper trader` — `*/5 10-18 * * 1-5`, no_agent
- Читает портфель из PG, проверяет сигналы, управляет позициями, сохраняет в PG
