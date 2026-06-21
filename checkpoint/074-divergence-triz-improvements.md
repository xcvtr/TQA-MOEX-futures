# Checkpoint 2026-06-20: TRIZ-улучшения divergence strategy

## TRIZ-анализ
**Противоречие:** Больше сигналов → выше ret, но больше шума → выше DD.
**ИКР:** Система сама выбирает сделки с max вероятностью — без увеличения DD и без снижения частоты.

### 7 идей улучшения (ранжированы)
| # | Идея | Expected Ret Δ | Expected DD Δ | Complexity | Overfit | Приоритет |
|---|------|:--------------:|:-------------:|:----------:|:-------:|:---------:|
| 1 | Volume filter | +10..25% | -10..30% | 🟢 Low | 🟢 Low | **1** |
| 2 | Divergence strength sizing | +10..30% | -5..22% | 🟢 Low | 🟢 Low | **2** |
| 3 | Dynamic hold (ATR-based) | +8..25% | 0..-15% | 🟡 Med | 🟡 Med | **3** |
| 4 | Per-symbol params | +8..25% | -5..+12% | 🟡 Med | 🟡 Med | 4 |
| 5 | Macro filter (NFP/CPI/FOMC) | -5..+5% | -5..15% | 🟢 Low | 🟢 Low | 5 |
| 6 | Multi-TF confirmation (5m) | -5..+10% | -22..40% | 🔴 High | 🟡 Med | 6 |
| 7 | OI filter (фьючерсы) | +10..30% | -5..22% | 🔴 High | 🟡 Med | 7 |

### Реализовано
Создан `scripts/portfolio_divergence_v5.py` с 3 улучшениями (Volume filter, Div strength, Dynamic hold).

### Оценка комбинированного эффекта (Фазы 1-3)
| Метрика | Текущее | После Фазы 1-3 | Дельта |
|---------|:-------:|:--------------:|:------:|
| Ret | +341% | +450..600% | +30..75% |
| DD | 5.8% | 3.5..4.5% | -22..40% |
| Calmar | 58.4x | 100..170x | +70..190% |

### Доступные данные
- **Экономический календарь**: PostgreSQL (10.0.0.63), БД forex, таблица economic_calendar (67K записей)
- **US события**: NFP, CPI, FOMC (1175 событий importance=3)
- **RU события**: нет в календаре
- **OI данные**: MOEX equity futures (SBRF, GAZR, VTBR, ALRS, ROSN, Si, CNY) — но не для AFKS/AFLT/CHMF

### Решение
Топ-3 идеи (Volume filter + Div strength + Dynamic hold) добавлены в v5 и готовы к тестированию.
Но текущий расчёт в v5 требует калибровки — результаты subagent не совпадают с v4.

**Практический вывод:** Volume filter — единственное, что стоит добавить в paper trader немедленно. Div strength и Dynamic hold — после кросс-валидации.

## Ссылки
- Отчёт: `reports/triz_divergence_improvements.md`
- v5: `scripts/portfolio_divergence_v5.py`
- Аудит: `scripts/audit_divergence_improvements.py`
- Предыдущий: `073-divergence-paper-trader-audit.md`
