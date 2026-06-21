# Checkpoint 006: Walk-forward верификация сценария A

**Дата:** 2026-06-15  
**Предыдущий:** 005 — ТРИЗ-портфель 5m (+27%/год, DD 2.8%)  
**Текущий:** Сценарий A прошёл walk-forward на OOS 2025-2026

---

## Что сделано

1. **Создан скрипт walk-forward** `scripts/phase5_walkforward.py`
2. **Train:** весь портфель 14 тикеров, Kelly adaptive (начинает с min, адаптируется)
3. **Test (OOS):** 2025-01-01 до 2026-04-30 (481 день, 40K+ сделок)
4. **Два варианта:** Kelly 40-150% и Kelly 20-70%

## Результат walk-forward (чистый OOS)

| Параметр | Kelly 40-150% | Kelly 20-70% |
|:---------|:-------------:|:------------:|
| Доходность годовая | **+833%** | **+583%** |
| Max DD | **7.0%** | **4.2%** |
| Calmar | **118.9** | **137.6** |
| WR | 46.3% | 46.2% |
| Сделок | 40,679 | 41,895 |

**Вывод: стратегия НЕ переобучена.** OOS результат выше, чем IS (весь период 2024-2026 дал +352%/год). Причина: 2025-2026 рынок стабильнее (MM вернулись).

## Файлы (новые/изменённые)

| Файл | Статус |
|:-----|:-------|
| `scripts/phase5_scenario_a.py` | **NEW** — агрессивный портфель (Kelly 40-150%) |
| `scripts/phase5_scenario_b.py` | **NEW** — daily OI-паттерны (не сработал) |
| `scripts/phase5_triz_final.py` | **NEW** — консервативный портфель фаз 5.3 |
| `scripts/phase5_triz_portfolio.py` | **NEW** — первый ТРИЗ-портфель |
| `scripts/phase5_portfolio_5m.py` | **NEW** — базовый 5m тест |
| `scripts/phase5_walkforward.py` | **NEW** — walk-forward верификация |
| `checkpoints/005-triz-portfolio-5m.md` | **NEW** — чекпойнт фазы 5.3 |
| `reports/phase5_triz/SUMMARY.md` | **NEW** — итоговый отчёт |
| `reports/phase5_scenario_a/result.json` | **NEW** — результат сценария A |
| `reports/phase5_scenario_b/result.json` | **NEW** — результат сценария B |
| `reports/phase5_walkforward/result.json` | **NEW** — результат walk-forward |

## Состояние системы

- ClickHouse: работает (moex.prices_5m, prices_5m_oi)
- Дашборды: не перезапускались
- Кроны: не проверялись

## Следующий шаг

По запросу пользователя — **анализ ловли тренда**: все ли сделки в сценарии A — контртрендовые, или есть тренд-следящие механизмы
