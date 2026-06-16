# TRIZ: OI Divergence на правильном таймфрейме

## Диагностика ошибки
OI Divergence тестировалась на 5m с horizon=12 (= 1 час). Это неправильно:
- Институциональные игроки (киты) работают на H1-D1
- 5m OI — шум, случайные флуктуации
- Реальный OI-сигнал: 1-2 в день, сделка держится несколько дней
- 20 сигналов/день = перешумление, 85% rollover = следствие неправильного ТФ

## Решение: многотаймфреймовый OI Divergence

### Шаг 1: Определить правильный ТФ
- H1: 1 бар = 1 час, horizon=24 → 24 часа = 1 день
- H4: 1 бар = 4 часа, horizon=12 → 48 часов = 2 дня
- D1: 1 бар = 1 день, horizon=5 → 5 дней = рабочая неделя

Для КАЖДОГО:
- Resample 5m OHLCV + OI до H1/H4/D1
- Запустить detect_oi_divergence_signals_limit
- Сколько сигналов/день? Если 1-3 → правильный ТФ

### Шаг 2: Тест через bar-level
Для ТФ с 1-3 сигналами/день:
- bar_level_sim.BarLevelPortfolio
- Параметры как у baseline
- Сравнить: return%, DD%, Calmar, exit_reasons

### Шаг 3: Walk-forward
Если Calmar > 1.0 → 4 folds

### Шаг 4: Комиссии
Если проходит → commission=2 RUB/контракт

## Требования
- Не использовать .signals_oi_div_limit.json (он 5m)
- Загружать сырые данные через load_ohlcv() + load_oi() для всех 47 тикеров
- Resample OHLCV + OI до H1, H4, D1
- Создать detect_signals для каждого ТФ
- Тест через BarLevelPortfolio
- Время: ~1-2 часа
- Без вопросов
- Отчёт: reports/YYYY-MM-DD-tf-oi-rescue.md
