# Checkpoint 104 — CVD Verdict: No Edge, TRIZ Analysis Complete

**Дата:** 2026-06-27
**Проект:** TQA-MOEX-futures
**Предыдущий:** #103b — CVD TP/SL methodology confirmed

---

## CVD: Вердикт

**CVD на 5m MOEX — чистый шум.** Корреляция dcvd_z с возвратом через 60 мин = −0.0029. WR=50% при любом пороге.

```python
P(up | dcvd_z>0.6) = 50.8%   # сигнал long
P(up | dcvd_z<-0.6) = 50.4%  # сигнал short
P(up | no signal) = 47.5%     # без сигнала
```

Разница 3pp — это тренд рынка, а не edge. После вычитания среднерыночного возврата excess_net80 ≈ 0 на 17/18 тикеров.

### Почему не работает

1. **CVD требует тиковых данных** — последовательность сделок. На 5m агрегатах информация о порядке сделок потеряна
2. **vol_b ≈ vol_s** на MOEX — корреляция ~0.99, disb < 0.05 в 95% баров
3. **ΔCVD z-score** — белый шум, не коррелирует с ценой

### Отрицательные результаты

- P80/P20 TP/SL: бессмысленно (сигнала нет)
- Оптимизация множителей: подгонка под шум
- Short-направление: не работает (11/18 убыточны)

---

## Архитектура (построено)

| Компонент | Статус |
|-----------|--------|
| PG схема `futures.*` | ✅ 64 ticker_specs |
| `futures.strategy_cvd_portfolio` | ✅ TP/SL + множители |
| PG схема `shared.*` | ✅ calendar (пусто) |
| `strategies/cvd/prod/` | ✅ engine + paper_trader + cron + dashboard |
| `strategies/cvd/dev/` | ✅ эксперименты |
| `strategies/cvd/scripts/` | ✅ analyze_tpsl, optimize, backtest, wf |
| `lib_cvd_divergence` → `strategies/cvd/prod/lib.py` | ✅ PG без хардкода |
| AGENTS.md | ✅ архитектура |
| Cron `cvd_paper_trader` | ✅ живёт в ~/.hermes/scripts/ |

---

## TRIZ-анализ (10 идей)

Полный отчёт: `reports/triz_moex_futures_analysis.md` (349 строк)

**Топ-3 для проверки:**

| # | Идея | Источник | Edge | Простота |
|---|------|----------|:----:|:--------:|
| 1 | **Stop Hunt Detection** | `alerts_fo` + price action | High | 🟢 |
| 2 | **Intraday OI Spike** | `tradestats_fo.oi_close` | High | 🟢 |
| 3 | **HHI + Price Divergence** | `hi2_fo` (1.14M) | Medium | 🟡 |

---

## Файлы

| Файл | Описание |
|------|----------|
| `strategies/cvd/engine.py` | CVD engine (immutable) |
| `strategies/cvd/lib.py` | PG loader, PnL, slippage |
| `strategies/cvd/prod/paper_trader.py` | paper executor |
| `strategies/cvd/scripts/analyze_tpsl.py` | P80/P20 анализ |
| `strategies/cvd/scripts/optimize_tp_mult_v3.py` | оптимизация множителей |
| `strategies/cvd/scripts/backtest_tpsl.py` | backtest TP/SL |
| `strategies/cvd/scripts/analyze_max_excursion_v2.py` | max excursion |
| `reports/triz_moex_futures_analysis.md` | TRIZ 10 идей |
| `reports/cvd_p80_tp_sl_results.json` | P80/P20 per ticker |
| `reports/cvd_max_excursion_v2.json` | max excursion per ticker |
| `reports/backtest_tpsl_results.json` | backtest TP/SL |

---

## Что дальше

1. **Stop Hunt Detection** — `alerts_fo` + ложные пробои уровней
2. **Intraday OI Spike** — экстремальные изменения OI
3. **HHI + Price Divergence** — концентрация MM vs цена
