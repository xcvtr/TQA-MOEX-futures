# ТРИЗ: Adaptive Risk Management — решение противоречия просадка/доходность

## Проблема
Статические параметры: один `margin_usage` на все режимы рынка.

## Решение (ТРИЗ принцип 15 — Динамичность)

Заменить статические `margin_usage` и `total_margin_limit` на **адаптивные**, зависящие от текущего состояния equity относительно пика.

### Формула

```python
# Коэффициент сжатия: 1.0 на пике, <1.0 в просадке
compression = current_equity / peak_equity  # 0..1

# Адаптивные лимиты
adaptive_margin_usage = base_margin_usage * compression
adaptive_total_margin = base_total_margin_limit * compression
```

Когда капитал на пике (compression=1.0) — полный размер позиций.
При просадке 5% (compression=0.95) — позиции сжимаются на 5%.
При просадке 20% (compression=0.80) — позиции сжимаются на 20%.

Это anti-fragile поведение: система автоматически уменьшает риск после реализации риска.

### Новые параметры grid search

```python
param_grid = {
    'base_margin_usage': [0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15],
    'max_concurrent': [2, 3, 5, 8],
    'base_total_margin_limit': [0.05, 0.08, 0.10, 0.15, 0.20, 0.30],
    'max_dd_limit': [0.05, 0.10],
    'stop_loss_pct': [0.005, 0.01, 0.015, 0.02],
}
```

### Изменения в simulate()

1. `base_margin_usage` — базовая доля капитала (при peak)
2. `base_total_margin_limit` — базовая максимальная суммарная маржа (при peak)
3. Каждый сигнал: пересчитать `adaptive_margin = base_margin × (equity/peak)`
4. Перед открытием: пересчитать `adaptive_tm = base_tm × (equity/peak)`
5. Остальная логика без изменений

### Ожидаемый эффект

- Просадка автоматически ограничивается — compression падает, риск сжимается
- После восстановления — полная мощность
- Можно позволить более высокий base_margin, т.к. система сама себя страхует

## Запуск

```bash
cd /home/user/projects/TQA-MOEX
python scripts/capital_growth_sim.py --sweep-adaptive
```

## Выходные файлы
- `docs/plans/capital_growth/pareto_adaptive_top10.csv`
- `docs/plans/capital_growth/equity_curve_adaptive.csv`
- `docs/plans/capital_growth/summary_adaptive.txt`

## Верификация (тщательно!)

После расчёта проверить:
1. Сравнить adaptive vs static на одинаковых параметрах — adaptive должен давать МЕНЬШЕ просадку
2. Взять TOP-1 adaptive params, прогнать без adaptive — должно быть больше просадка (иначе adaptive не работает)
3. Проверить 3 случайные сделки: entry, exit, pnl по формуле из tracker.py
4. Проверить что compression никогда не падает ниже 0.5 (защита от бесконечного сжатия)
5. Проверить что при equity > peak, compression=1.0 (не даёт >1.0)
