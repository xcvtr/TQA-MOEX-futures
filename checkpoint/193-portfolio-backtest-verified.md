# Checkpoint 193: Portfolio backtest verified — +8,593% ROI, MDD 7.92%

**Дата:** 2026-07-30  
**Теги:** checkpoint, backtest, portfolio, verified

## Результаты бэктеста (365 дней, FINAM данные, common pool)

**Капитал:** 200,000 → **17,386,856** (+8,593.4%)
**Сделок:** 4,419 | **Cash MDD:** 7.09% | **MTM MDD:** 7.92% ✅
**Источник данных:** CH `moex.mt5_continuous` (FINAM MT5 bridge)

## Состав портфеля

| Стратегия | Тикер | Detect | Risk | Сделок | WR | PF | PnL |
|:----------|:------|:-----:|:----:|:------:|:--:|:--:|----:|
| ⚡ IR | Si | M1 | 10% | 502 | 49.6% | 2.40 | +1,751K |
| 🐉 Dragon | GD | 10m | 20% | 145 | 56.6% | 3.50 | +1,536K |
| 🐉 Dragon | MM | 5m | 15% | 205 | 51.7% | 1.84 | +24K |
| 🛑 **SH** | **RN** | **M1** | **20%** | **2,710** | **46.5%** | **19.45** | **+13,525K** 🔥 |
| 🐉 Dragon | NG | 3m | 20% | 857 | 47.0% | 1.85 | +390K |

## Аудит
- **Look-ahead bias:** нет (lo_hist[:-1], close_hist[:-1])
- **Комиссия:** per-ticker fee_entry из PG (Si=3.81, GD=44.28, MM=1.51, RN=7.22, NG=4.0)
- **Slippage:** entry close + 2-5 tick; exit по рынку + 1 tick
- **Trend filter:** SMA50 без текущего бара
- **Данные:** FINAM MT5 → CH `moex.mt5_continuous` (актуальные)
- **Методика:** Detect ≠ Tick (resample M1 → N-min, tick на M1)

## Портфель в PG

```sql
futures.portfolio (enabled=true):
  Si  → impulse_return  (trend)
  GD  → dragon           (trend)
  MM  → dragon           (trend)
  RN  → stop_hunt        (lookback=60, retrace=0.05)
  NG  → dragon           (trend)
```

## Состояние paper trader
- v5: 0 позиций, Equity 200 000₽ (сброшен)
- v6: 0 позиций, Equity 200 000₽ (сброшен)
- Оба используют портфель из PG (5 стратегий)
- Cron: `*/5 15-23,0-4 * * 1-5`
