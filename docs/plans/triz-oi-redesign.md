# TRIZ-переосмысление: OI Divergence — найти настоящий edge

## Исходные данные
- OI Divergence limit orders: entry по low/high бара (реальный edge), exit по close через 12 баров
- bar-level sim с MTM: +3.5% return, потому что 85% сделок — rollover (закрыты новым сигналом до exit)
- SMA mean reversion на CE: +38% за 3.5 года (слишком мало)

**TRIZ-диагностика:**

**ПРОТИВОРЕЧИЕ:** Сигналов много → rollover убивает прибыль, потому что старые позиции закрываются по цене нового сигнала (часто хуже exit_price). Фильтровать сигналы → мало сделок, нет диверсификации.

**ИКР:** Система, где сигналы редкие, но точные. Каждая позиция живёт до exit_price без rollover.

**Корень:** 7093 сигнала за 342 дня = ~20 сигналов/день. При max_concurrent=5, лишние сигналы только создают rollover — капитал тратится на замену, а не на удержание.

## План: 4 TRIZ-решения

### Решение А: Score Filtering (Отбрасывание)
Сигнал: OI Divergence limit, но ТОЛЬКО score > percentile.
- Test: score_percentile=[50, 75, 90, 95] (медиана, топ-25%, топ-10%, топ-5%)
- Для каждого: сколько сигналов остаётся, return%, DD%, Calmar
- Гипотеза: топ-10% сигналов (score > 0.8) дадут БОЛЬШЕ прибыли чем все 100%

### Решение Б: Daily timeframe (Переход в другое измерение)
Сигнал: OI Divergence на DAILY данных (resample 5m→D).
- Entry: limit по low/high daily бара
- Exit: close через N=12 daily баров
- Stop-loss: 10% (широкий)
- MU=0.50, hold=12 days
- Сравнить: daily vs 5m результаты

### Решение В: ATR Trailing (Динамичность)
Вместо exit_price (фиксированная цель) — trailing stop по ATR.
- Entry: OI Divergence limit (5m)
- Exit: когда цена пересекает ATR-канал от пика
- trail_mult = [2.0, 3.0, 4.0, 5.0]
- Позволяет winners бежать дольше, а losers закрывать раньше

### Решение Г: OI + SMA фильтр (Матрёшка)
Комбинация: OI Divergence LIMIT entry + SMA trend filter on daily.
- LONG: только если daily SMA5 > SMA20 (восходящий тренд)
- SHORT: только если daily SMA5 < SMA20 (нисходящий тренд)
- Отсекает контр-трендовые сигналы, оставляет только по тренду
- Гипотеза: меньше сделок, но выше WR

## Формат вывода
Для КАЖДОГО решения:
1. Чёткая таблица: вариант | return% | DD% | Calmar | trades | WR
2. Сравнение с baseline (OI Div Limit base: +3.5% DD=24.46%)
3. Если Calmar > 1.0 → walk-forward 4 folds
4. Если walk-forward проходит → report

## Требования
- Все тесты через bar_level_sim.BarLevelPortfolio (5m, OHLCV close, MTM, stop-loss)
- OI сигналы из .signals_oi_div_limit.json (7093 сигнала)
- Для daily: resample 5m → D, пересчитать сигналы
- Время: ~2 часа
- Без вопросов — делай всё сам
- Отчёт: reports/YYYY-MM-DD-triz-oi-redesign.md
