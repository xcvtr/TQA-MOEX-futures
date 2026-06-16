# План: 4 направления доводки BR Mean Reversion

## Направление 1: Комиссии MOEX
**ИКР:** Узнать реальную доходность после всех издержек.

Сделать:
- Прочитать текущую стратегию scripts/br_contango_strategy.py — понять сколько сделок и сколько контрактов
- Комиссия MOEX на фьючерсы BR:
  - Тикер: BR-9.25 (Brent)
  - Комиссия: ~1-5 RUB за контракт (скальперская), ~0.5-1 RUB для обычной
  - Для расчёта взять 2 RUB за контракт за сделку (entry + exit = 4 RUB)
- Добавить в DailyPortfolio параметр `commission_per_contract: float = 2.0`
- При каждой сделке вычитать: `commission_per_contract * contracts * 2` (entry + exit)
- Запустить симуляцию с комиссией
- Вывести: return% с комиссией, DD%, сколько съедено комиссией

Скрипт: `scripts/commission_impact.py`

## Направление 2: Фильтры входа
**ИКР:** Улучшить win rate с 59% до 65%+ без снижения числа сделок.

Сделать проверку 4 фильтров на BR daily данных (2023-2026):

А) **Volatility filter**: не входить, если ATR(14) > median*1.5 (слишком волатильно)
Б) **ADX filter**: входить только при ADX(14) > 20 (трендовый режим)
В) **Macro filter**: не входить за 1 день до и 2 дня после календарных событий (ставка ЦБ, нефть OPEC) — для этого использовать данные экономического календаря из БД или пропустить
Г) **Volume filter**: входить только если volume > SMA_volume(20) * 0.8 (подтверждение активности)

Для КАЖДОГО фильтра:
- Запустить симуляцию
- Сравнить с базой: return%, DD%, Calmar, trades, win rate
- Вывести таблицу: фильтр | return | DD | Calmar | trades | WR

Лучший фильтр (или комбинацию) — протестировать через walk-forward.

Скрипт: `scripts/filter_optimization.py`

## Направление 3: Другие инструменты
**ИКР:** Найти другие tickers, где SMA5<SMA20 mean reversion тоже работает.

MOEX фьючерсы для проверки (daily, 2023-2026):
- Si (USD/RUB)
- RB (Brent — уже есть)
- RI (RTS index)
- AU (Gold)
- ED (Eurobond)
- CNYRUBF (CNY/RUB)
- IMOEXF (MOEX index)

Для каждого:
- Сигнал: SMA5 < SMA20 → LONG, hold=5, sl=0.10, mu=0.50
- Симуляция через DailyPortfolio
- Walk-forward 4 folds
- Вывести: ticker | return% | DD% | Calmar | trades | WR | WF passes

Скрипт: `scripts/multi_ticker_test.py`

## Направление 4: Ensemble
**ИКР:** Объединить лучшие инструменты в портфель для снижения DD и роста return.

- Взять топ-N инструментов из направления 3 (где Calmar > 1.0)
- Каждый инструмент получает равную долю капитала (100K / N)
- Ensemble портфель: все стратегии работают одновременно
- DailyPortfolio для ensemble: на каждом дне суммировать PnL всех активных позиций
- Сравнить ensemble vs best single ticker

Скрипт: `scripts/ensemble_test.py`

## ФОРМАТ ВЫВОДА
Каждый скрипт:
1. Выводит таблицу результатов
2. Сохраняет отчёт в reports/YYYY-MM-DD-nazvanie.md
3. Если результат лучше базы — рекомендует новые параметры

## ТРЕБОВАНИЯ
- Все тесты через DailyPortfolio (OHLCV close, MTM, stop-loss)
- Walk-forward для направлений 2-4
- No look-ahead bias
- Каждый скрипт самодостаточен (импорты внутри)
- Время выполнения каждого ≤ 10 минут
