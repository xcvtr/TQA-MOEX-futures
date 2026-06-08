# План: Моделирование разгона депозита (100K → ?) с walk-forward риск-менеджментом

## Цель

Смоделировать торговлю портфеля из 5 стратегий на MOEX фьючерсах за 2 года исторических данных. Старт: 100 000 RUB. Режим: агрессивный разгон (максимальная скорость роста) с контролем риска слива. Walk-forward оптимизация параметров риск-менеджмента.

## Стратегии (5 штук, production configs)

Все стратегии используют limit entry и per-ticker TF. Конфиги — в `trading_bot/__init__.py`:
- `OB_TICKERS` — Order Block (15 тикеров, H1/H2/H4)
- `VWAP_TICKERS` — VWAP Deviation (4 тикера, 5m)
- `REVERSION_TICKERS` — Mean Reversion (2 тикера, 5m/15m)
- `OI_DIVERGENCE_TICKERS` — OI Divergence (3 тикера, 5m)
- `TICKERS` — Volume Surge (4 тикера, 5m/15m/H1)

Все конфиги читать из `trading_bot/__init__.py`.

## Архитектура решения

Создать 1 скрипт: `scripts/capital_growth_sim.py`

### Этап 1: Сбор всех исторических сигналов (функция collect_all_signals)

Для каждой стратегии:
1. Загрузить все доступные данные (730 дней)
2. Прогнать через detect_*_signals_limit функцию
3. Сохранить сигналы с полями: time, ticker, direction, entry, exit, return_pct, strategy

Сигналы сортируются по времени глобально. Каждый сигнал имеет return_pct — процент изменения цены от входа до выхода (уже правильно: LONG = (exit-entry)/entry*100, SHORT = (entry-exit)/entry*100).

**ВАЖНО**: Не использовать существующий tracker.py / positions.json — это всё для paper trading в реальном времени. Симуляция — отдельный скрипт.

### Этап 2: Risk-менеджмент модель

**Ключевая формула позиционирования:**
```
max_contracts = floor(capital * margin_usage / GO)
```
Где:
- `capital` — текущий капитал (начинается с 100K, растёт/падает)
- `margin_usage` — доля капитала, выделяемая на 1 сделку (параметр оптимизации)
- `GO` — гарантийное обеспечение контракта из конфига тикера

**PnL расчёт:**
```
price_moves = (exit - entry) / minstep
if SHORT: price_moves = -price_moves
pnl_rub = price_moves * tick_rub * contracts
```

**Ограничения (hard rules):**
1. Не больше `max_concurrent_positions` открытых позиций одновременно (параметр)
2. Если drawdown превышает `max_dd_limit` — торговля останавливается до конца семпла
3. Один тикер — только 1 открытая позиция (не пересекаться)
4. Сигналы обрабатываются последовательно по времени

### Этап 3: Walk forward (4 фолда)

Разбить историю (2 года) на 4 равных периода по ~6 месяцев.

Walk forward схема:
```
Fold 1: train [2024-06 .. 2024-12] → test [2024-12 .. 2025-06]
Fold 2: train [2024-12 .. 2025-06] → test [2025-06 .. 2025-12]
Fold 3: train [2025-06 .. 2025-12] → test [2025-12 .. 2026-06]
Валидация на OUT-OF-SAMPLE данных: Fold 1+2+3 test периоды конкатенированы
```

**На train сегменте:**
Для каждого набора параметров риск-менеджмента запустить симуляцию, измерить:
- Конечный капитал
- Максимальная просадка (max drawdown %)
- Sharpe ratio
- Коэффициент восстановления (return / maxDD)

**Параметры для оптимизации (grid search):**
```python
param_grid = {
    'margin_usage': [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5],  # доля капитала на 1 сделку
    'max_concurrent': [1, 2, 3, 5],  # макс одновременных позиций
    'max_dd_limit': [0.15, 0.20, 0.25, 0.30],  # стоп торговли при просадке
}
```

Всего: 7 × 4 × 4 = 112 комбинаций на фолд.

**Метрика отбора лучшей комбинации:**
```python
score = final_capital * (1 - max_drawdown)  # штрафуем за просадку
```
Или вариант: Calmar ratio = return / max_drawdown.

Берём комбинацию с максимальным score на train → применяем на test.

### Этап 4: Симуляция разгона (функция simulate)

```python
def simulate(signals, initial_capital, margin_usage, max_concurrent, max_dd_limit):
    """
    signals: list[dict] — отсортированы по времени, каждый с:
        time, ticker, direction, entry, exit, return_pct, strategy
    
    Returns: dict с equity_curve, stats
    """
    capital = initial_capital
    equity_curve = [capital]
    peak = capital
    active_positions = {}  # ticker -> {entry, contracts, direction, entry_time}
    
    for i, sig in enumerate(signals):
        ticker = sig['ticker']
        
        # Проверка: превышен лимит просадки?
        dd = (peak - capital) / peak
        if dd > max_dd_limit:
            # Стоп торговли — не открываем новые позиции, но закрываем существующие
            # Пропускаем новые сигналы до конца
            continue
        
        # Если по этому тикеру уже есть открытая позиция — пропускаем
        if ticker in active_positions:
            continue
        
        # Проверка лимита concurrent
        if len(active_positions) >= max_concurrent:
            continue
        
        # Расчёт количества контрактов
        cfg = ticker_config(ticker)
        go = cfg['go']
        max_risk = capital * margin_usage
        contracts = floor(max_risk / go)
        if contracts < 1:
            continue
        
        # Открываем позицию
        active_positions[ticker] = {
            'entry': sig['entry'],
            'direction': sig['direction'],
            'contracts': contracts,
            'entry_time': sig['time'],
            'exit': sig['exit'],
        }
        
        # Закрываем позицию (в этой симуляции сигнал уже содержит exit)
        # Сразу закрываем — return_pct уже включает horizon
        pos = active_positions.pop(ticker)
        pnl_rub = calc_pnl(pos['direction'], pos['entry'], pos['exit'], pos['contracts'], ticker, cfg)
        capital += pnl_rub
        
        # Обновляем peak для drawdown
        peak = max(peak, capital)
        equity_curve.append(capital)
    
    return {
        'final_capital': capital,
        'total_return': (capital - initial_capital) / initial_capital * 100,
        'max_drawdown': max_drawdown(equity_curve),
        'total_trades': len([e for e in equity_curve if e != equity_curve[0]]),
        'equity_curve': equity_curve,
        'sharpe': calc_sharpe(equity_curve),
    }
```

**Важный момент**: Сигнал приходит → сразу открываем и закрываем (return_pct уже содержит полный цикл entry→exit за horizon баров). Но concurrent ограничение работает: мы не можем открыть новый сигнал по тому же тикеру, пока не закрыт предыдущий.

### Этап 5: Результаты

Для каждого фолда вывести:
- Лучшие параметры на train + их score
- Результаты на test: начальный капитал → конечный, return%, maxDD, Sharpe

Финальный результат:
- Конкатенированные test периоды всех фолдов → единая equity curve
- Итоговый капитал
- Общая доходность
- Максимальная просадка
- Sharpe ratio
- Calmar ratio

### Выходные файлы

1. `docs/plans/capital_growth/results.json` — полные результаты по всем фолдам
2. `docs/plans/capital_growth/equity_curve.csv` — equity curve (время, капитал)
3. `docs/plans/capital_growth/summary.txt` — текстовый отчёт
4. `docs/plans/capital_growth/optimal_params.csv` — лучшие параметры по каждому фолду

### Пример расчётов (для верификации)

Если на train нашлась комбинация margin_usage=0.25, max_concurrent=2:
- Старт: 100 000
- 1-й сигнал: UC LONG, entry=5000, GO=5000, minstep=0.01, tick_rub=1.0
  - contracts = floor(100000 * 0.25 / 5000) = 5
  - return_pct = +0.5%
  - pnl_rub = (5000*1.005 - 5000) / 0.01 * 1.0 * 5 = 2500 * 5 = 1250 RUB (wait, that's wrong)
  
  Actually let me recalculate. If entry=5000, exit=5025: 
  - moves = (5025-5000)/0.01 = 2500
  - pnl_rub = 2500 * 1.0 * 5 = 12500 RUB
  - return_pct = (5025-5000)/5000*100 = 0.5%
  
  Hmm, the PnL seems high because minstep=0.01 for UC means each 0.01 price move = 1 RUB.
  
  Actually for the PnL calculation, let me just use the tracker's existing `_calc_pnl` function or replicate it.

### Верификация

После выполнения скрипта проверить:
1. Equity curve не содержит отрицательных значений (капитал не может быть ниже 0)
2. Количество сделок разумное (не меньше 100 за 2 года для всего портфеля)
3. Максимальная просадка не превышает лимит (max_dd_limit должен работать)
4. Walk forward корректный — train/test не пересекаются по времени
5. Хотя бы одна комбинация параметров даёт положительную доходность
6. Для одного случайного тикера вручную пересчитать PnL по формуле
