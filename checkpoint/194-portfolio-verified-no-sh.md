# Checkpoint 194: Portfolio verified без SH RN — +1,228% ROI, MDD 14.31%

**Дата:** 2026-07-30
**Теги:** checkpoint, backtest, portfolio, verified

## Результаты бэктеста (365 дней, FINAM mt5_continuous, common pool)

**Капитал:** 200,000 → **2,657,133** (+1,228.6%)
**Сделок:** 1,709 | **Cash MDD:** 14.30% | **MTM MDD:** 14.31% ✅

## Состав портфеля (4 стратегии, без SH RN)

| Стратегия | Тикер | Detect | Risk | Сделок | WR | PF | PnL |
|:----------|:------|:-----:|:----:|:------:|:--:|:--:|----:|
| ⚡ IR | Si | M1 | 10% | 502 | 49.6% | 2.39 | +1,595K |
| 🐉 Dragon | GD | 10m | 20% | 145 | 56.6% | 3.71 | +515K |
| 🐉 Dragon | MM | 5m | 15% | 205 | 51.7% | 1.84 | +24K |
| 🐉 Dragon | NG | 3m | 20% | 857 | 47.0% | 1.87 | +362K |

## Аудит
- **SH RN удалён** — независимая проверка показала PF=0.50 (убыточна)
- **backtest.py не используется** — имеет фундаментальные баги (lo_hist=30, M1/M5 miscale)
- **portfolio_run.py** — единственный валидный backtest (correct lo_hist, per-ticker TF, M1 tick)
- **Комиссия:** per-ticker fee_entry, round-trip
- **SL/TP:** trailing от пика, timeout, stop loss
- **Trend filter:** SMA50 на detect барах

## Портфель в PG

```sql
futures.portfolio (enabled=true):
  Si → impulse_return
  GD → dragon
  MM → dragon  
  NG → dragon
```

## Состояние paper trader
- Оба трейдера сброшены, Equity 200K
- Используют портфель из PG (4 стратегии)
