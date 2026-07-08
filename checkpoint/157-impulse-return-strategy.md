---
title: "Impulse Return — новая стратегия: вход на Фибо-коррекции после импульса"
checkpoint: 157
date: 2026-07-08
tags: [checkpoint, tqa-moex-futures, impulse-return]
---

# 157 — Impulse Return: вход на откате после импульса

## Что изменилось

- **Новая стратегия `strategies/impulse_return/`** — вход при коррекции 61.8% после импульса 0.5% за 4 бара (20 мин)
- **`strategies/common/backtester.py`** — impulse_return добавлен в STRATEGY_MAP
- **`strategies/common/engine.py`** — close_hist, vol_hist добавлены в _build_bar
- **`strategies/impulse_return/prod/engine.py`** — check_signal (pure math)
- **`strategies/impulse_return/run_backtester.py`** — портфельный тест через Backtester

## Ключевые метрики

### Impulse Return (2024-06 → 2026-06, 2 года, 200K, 1 contract)

```
┌─────────────────────────────────────────────────┐
│           IMPULSE RETURN — портфельный тест      │
├─────────────────────────────────────────────────┤
│ WR: 64.0%                                       │
│ Trades: 20,026                                   │
│ PnL: +29,078,754 ₽                              │
│ Return: +14,539%                                │
│ MTM MDD per ticker: 1.8-13.3%                   │
│ CAPEX: КНУР, per-ticker комиссии 4-15₽          │
│ Entry: close[i] + 1 tick slippage               │
│ Exit: timeout 60 мин (open[i+12])                │
└─────────────────────────────────────────────────┘
```

### Per-ticker

| Ticker | Trades | WR% | PnL | MTM MDD |
|:------|:-----:|:---:|:---:|:-------:|
| MIX | 3,358 | 68.0% | +13.7M | 13.3% |
| Si | 5,164 | 67.3% | +10.0M | 8.0% |
| GAZR | 4,024 | 62.7% | +1.6M | 5.0% |
| LKOH | 2,065 | 60.3% | +1.0M | 6.0% |
| ROSN | 2,002 | 60.9% | +1.2M | 9.0% |
| SNGP | 1,298 | 62.5% | +943K | 7.9% |
| MTSI | 1,142 | 59.6% | +337K | 1.8% |
| TATN | 973 | 59.9% | +335K | 6.1% |

### Сравнение со Stop Hunt

| Стратегия | WR | PnL/2года | MTM MDD |
|:----------|:--:|:---------:|:-------:|
| Stop Hunt (1 ctr) | 70.2% | +405K | 20.4% |
| **Impulse Return (1 ctr)** 🏆 | **64.0%** | **+29M** | **~8%** |

Stop Hunt имеет более высокий WR, но низкую частоту сигналов. Impulse Return — 20K сделок vs 517.

## Параметры стратегии

| Параметр | Значение | Пояснение |
|:---------|:---------|:----------|
| impulse_bars | 4 | Окно импульса (20 мин на 5-min данных) |
| impulse_pct | 0.5% | Минимальное движение для импульса |
| retrace | 0.618 (Фибо) | Коррекция от экстремума для входа |
| cooldown | 24 бара (2ч) | Пауза между сигналами |
| min_vol_pct | 0.8× median | Фильтр низкого объёма |
| exit | timeout 12 bars (60 мин) | Выход через час |

## Файлы

- `strategies/impulse_return/prod/engine.py`
- `strategies/impulse_return/run_backtester.py`
- `strategies/common/backtester.py` (+ impulse_return в STRATEGY_MAP)
- `strategies/common/engine.py` (+ close_hist, vol_hist)

## Состояние для продолжения

- Stop Hunt — работает (prod)
- CVD — отключён (шум)
- Churn, Lunch Reversal — отключены
- **Impulse Return** — добавлен, протестирован

## Что дальше

1. Composite: Stop Hunt + Impulse Return в портфеле
2. Live paper trader через Alor API
3. Подбор оптимального портфеля (веса тикеров)
