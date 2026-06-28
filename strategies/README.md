# Portfolio Engine — универсальный loop для всех стратегий

## Принцип

Один loop по барам. На каждом баре вызывает **все активные стратегии**.
Каждая стратегия: `check_signal(bar, ticker) → Signal | None`.

```python
for bar in bars:
    for strategy in active_strategies:
        for ticker in strategy.tickers:
            signal = strategy.check_signal(bar, ticker)
            if signal:
                portfolio.process(signal)
    portfolio.manage_positions(bar)  # trailing TP, timeout
```

## Структура

```
strategies/
  common/
    engine.py        ← портфельный loop
    trailing_tp.py   ← общий Trailing TP
    portfolio.py     ← управление позициями

  stop_hunt/
    prod/engine.py   ← check_signal() — Stop Hunt логика

  churn/
    prod/engine.py   ← check_signal() — Churn логика

  lunch_rev/
    prod/engine.py   ← check_signal() — Lunch Reversal

  cvd/
    prod/engine.py   ← check_signal() — CVD (уже есть)
```

Каждый `engine.py`:
- Не знает про портфель, позиции, капитал
- Только: `check_signal(bar_data, ticker, params) → Signal | None`
- Сигнал: `{direction, entry_price, reason, score}`

`common/engine.py`:
- Читает конфиг портфеля (PG `futures.strategy_portfolio` или CSV)
- На каждом баре: все стратегии → все тикеры → сигналы → позиции
- Управляет ГО, реинвестом, пересечением позиций
- Вызывает `common/trailing_tp.py` для каждой открытой позиции

## Преимущества

1. Новая стратегия = новый `strategies/xxx/prod/engine.py` с `check_signal()`
2. Портфельный loop не меняется
3. Trailing TP общий для всех
4. Конфиг портфеля — одна PG таблица
