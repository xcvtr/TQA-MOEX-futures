# Checkpoint 100 — CVD + YURz Filter Portfolio (Sequential)

**Дата:** 2026-06-27
**Проект:** TQA-MOEX-futures
**Стратегия:** CVD divergence + YURz (yur_net z-score) filter
**Тикеры:** NG, BR, Si
**Источник данных:** AlgoPack tradestats_fo (ClickHouse), prices_5m_oi (ClickHouse)

---

## Конфигурация OI-фильтра

```python
OI_FILTER_CONFIG = {
    'NG': {'tf_min': 240, 'yur_z': 2.0},   # 4h, z-score >= 2.0
    'BR': {'tf_min': 15,  'yur_z': 2.0},   # 15m, z-score >= 2.0
    'Si': {'tf_min': 60,  'yur_z': 1.0},   # 1h, z-score >= 1.0
}
```

- Сигнал: CVD divergence (price_chg vs cvd_cum_chg)
- YURz = z-score(yur_buy - yur_sell) на prices_5m_oi, resample на tf_min
- Конфлюэнс: направление сигнала совпадает с yur_net_chg
- Порог: |yur_net_z| >= yur_z

## Результат портфеля (sequential, upper bound)

| Метрика | Значение |
|---------|----------|
| Trades | 3,124 |
| WR | 79.4% |
| CAGR | 1,296% |
| Max DD | 8.82% |
| Calmar | 146.9 |

## Per ticker

| Тікер | TF | YURz | Сделок | WR | PnL |
|-------|-----|-------|--------|-----|------|
| NG | 4h | ≥2.0 | 670 | 82.1% | +7.96M |
| BR | 15m | ≥2.0 | 1,247 | 78.1% | +11.9M |
| Si | 1h | ≥1.0 | 1,207 | 79.2% | +34.7M |

## ⚠️ Важно

**Это sequential-PnL артефакт.** Каждый трейд использует весь доступный капитал. В реальности 3 тикера дают сигналы одновременно → капитал делится, плечо меньше. Sequential DD 8.82% в реальности будет 20-30%+ из-за наложения позиций.

**Что нужно:** MTM (mark-to-market) bar-by-bar симуляция с распределением капитала между одновременными сигналами.

## Что дальше

- [ ] MTM-бэктест портфеля с распределением капитала
- [ ] Сравнение with/without YURz-фильтра
- [ ] Оптимизация порогов yur_z per ticker
- [ ] Подключение к paper trader

---

## 2026-06-27: Исправление багов и верификация на 2025

### Починены баги в `scripts/mtm_portfolio_cvd_yur.py`:

1. **Группировка signals** — `collect_signals()` возвращает плоский список, `run_mtm_portfolio()` ожидает `dict[ticker → list]`. Добавлена группировка по ticker.
2. **Неимпортированный `timedelta`** — NameError на `timedelta(hours=1)` внутри `run_mtm_portfolio`. Добавлен `from datetime import timedelta`.
3. **Timezone mismatch** — `data['time']` из CH (Asia/Irkutsk), `bar_time` tz-naive → TypeError. Исправлено в `load_cvd_data()` через `.dt.tz_localize(None)`.

### Тестовый прогон (2025-01-01 → 2025-03-01, capital=100K, lk=60, q=0.6, hold=5):

| Mode | Trades | Final RUB | Return | MaxDD | Sharpe | WinRate |
|------|--------|-----------|--------|-------|--------|---------|
| with YURz | 39 | 99,815 | -0.19% | 3.97% | -0.069 | 38.5% |
| without YURz | 18 | 99,858 | -0.14% | 2.08% | -0.046 | 38.9% |

**Exit code 0**, без ошибок.
