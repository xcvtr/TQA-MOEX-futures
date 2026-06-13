# 048 — GD Портфель: 2 паттерна, walk-forward, аудит

## Итог аудита
Из 8 кандидатов, прошедших паттерн-поиск по 64 тикерам, аудит (look-ahead + B&H + вневыборка 2023) прошли только:
- **GD vol_up_oi_down**: вневыборка +3.7K, 82% от B&H ✅
- **GD vol_up_yb_up_fiz_down**: вневыборка +2.9K, 50% от B&H ✅
- NA, RL — нет вневыборки, риск
- GL, PT, W4 — не прошли вневыборку ❌

## Портфель GD (p1_or_p2)
Два паттерна, 50/50 капитала, hold=2, sl=2%, 200K, walk-forward 4 folds:
- Fold 1: +6.7%, DD 6.9%, WR 61%
- Fold 2: +4.4%, DD 4.7%, WR 59%
- Fold 3: +7.2%, DD 7.6%, WR 58%
- Fold 4: +11.9%, DD 17.9%, WR 63%
- **Средняя: +7.6%, DD 9.3%, WR 60%** — все 4 folds положительные

## Что дальше
Цель: 300% годовых с реинвестом.
Текущий результат: ~23% годовых.
Нужен TRIZ-подход и делегирование для поиска улучшений.

## Файлы
- `_pattern_search.py` — поиск паттернов по 64 тикерам
- `_pattern_backtest.py` — walk-forward по кандидатам
- `_audit_candidates.py` — look-ahead + B&H + вневыборка
- `_gd_portfolio_grid.py` — grid search GD портфеля
- `_gd_portfolio_report.py` — детальный отчёт OpenCode
- `reports/gd_portfolio_report.md` — отчёт (1048 строк)
- `reports/gd_portfolio_grid.json` — 109 комбинаций
- `reports/pattern_backtest.json` — результаты WF
