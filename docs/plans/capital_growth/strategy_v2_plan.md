# Стратегический план: 10x за 1 год при DD≤10% на MOEX

## Контекст

Проект: TQA-MOEX (фьючерсы Московской биржи)
Капитал: 100 000 RUB → цель 1 000 000 RUB за 1 год
DD ≤ 10% (жёсткое ограничение)

Текущий best: +57.66% за 2 года (DD=5.43%) с OI Divergence + Mean Reversion
Разрыв: нужно ~900% за 1 год. Текущие стратегии дают ~25% годовых.

## Текущие стратегии + их проблемы

### OI Divergence (830 сигналов, WR=~50%, avg PnL=~350 RUB)
Тикеры: RI, GL, Si
Смысл: расхождение между ценой и OI → разворот
Проблема: WR ниже 55%, много ложных сигналов

### Mean Reversion (281 сигналов, WR=~45%, avg PnL=~21 RUB)
Тикеры: NM, AF
Смысл: z-score откат к скользящей средней
Проблема: слишком маленький avg PnL, стоп-лосс 0.5% режет почти всё

### Volume Surge (DISABLED — WR=46.3%, avg -0.067%)
Была отключена из-за отрицательного edge

### Order Block (DISABLED)
Была отключена из-за необходимости переработки SL

### VWAP Deviation (SKIPPED)
Была пропущена из-за 97% сигналов → доминирование

## 6-часовая программа работ

### Фаза 1: Диагностика (30 мин)
- На каждый отключенный тикер/стратегию: загрузить данные за 2 года
- Пересчитать WR, PF, avg return, max DD
- Определить: можно ли спасти (другой TF, другие параметры) или удалить
- Сохранить в `docs/strategy_diagnostics.txt`

### Фаза 2: Новые стратегии (3 часа)

Разработать и протестировать КАЖДУЮ из этих 6 стратегий:

#### 1. Whale Detection (OI Volume Burst)
**Идея:** Всплеск yur_buy или yur_sell (институциональные объёмы) на фоне среднего/низкого fiz → крупный игрок входит → цена продолжит движение.
**Измерение:** z-score yur_buy относительно 20-периода, порог 2.5σ
**Вход:** на открытии следующего 5m бара после всплеска
**Выход:** через 12 баров или стоп 1.5%

#### 2. Regime Filter
**Идея:** Фильтровать ВСЕ стратегии: торговать только в сильный тренд (ADX>25), не торговать в боковике (ADX<15).
**Измерение:** ADX(14) на H1
**Внедрение:** Обёртка над всеми существующими стратегиями

#### 3. OI Divergence v2 (with ATR bands)
**Идея:** Текущая OI Divergence слишком чувствительна. Добавить ATR-фильтр: сигнал только если цена ПРОБИЛА ATR-канал + OI diverges.
**Измерение:** ATR(14) × 1.5 от средней цены = канал
**Вход:** close выше/ниже канала + OI divergence

#### 4. Spread Trading (pair trading)
**Идея:** Пары коррелированных инструментов (Si/BR, RI/GL, NM/AF). z-score спреда = вход.
**Измерение:** 60-минутный спред, z-score > 2.0 = SHORT спред, < -2.0 = LONG спред
**Вход:** на открытии следующего бара
**Выход:** z-score < 0.5

#### 5. Price + Volume Profile (Value Area)
**Идея:** High Volume Node (HVN) = поддержка/сопротивление. Цена отскакивает от HVN.
**Измерение:** Volume Profile за 20 периодов, HVN = уровень с volume > 2σ от среднего
**Вход:** цена на HVN + подтверждение (close в направлении от HVN)

#### 6. Momentum Breakout with OI Confirmation
**Идея:** Пробой 20-периодного максимума/минимума + OI растёт (участники подтверждают тренд).
**Измерение:** close = новый High(20) + OI change > 0 и yur_buy > fiz_buy
**Вход:** на открытии следующего 5m бара
**Выход:** через 24 бара или стоп 2%

### Фаза 3: Композитный портфель (1 час)
- Объединить ВСЕ стратегии в единый pipeline
- Каждая стратегия даёт свой total_margin_limit
- Walk-forward отбор: какие стратегии включить на данном режиме рынка
- Запустить полный sweep с adaptive risk
- Цель: +900% за 1 год при DD≤10%

### Фаза 4: Оптимизация (1 час)
- Для каждой живой стратегии: grid search по TF (5m, 15m, 30m, H1) и параметрам
- Per-ticker оптимизация (не все стратегии работают на всех тикерах)
- Отсев тикеров с WR < 52% или avg return < 0

### Фаза 5: Cross-check (30 мин)
- Верификация PnL по 3 случайным сделкам каждой стратегии
- Сравнение equity adaptive vs static
- Проверка look-ahead bias

## Критические ограничения
- NO look-ahead bias — z-score, rolling median, ATR — только на прошлых данных
- Все стратегии проверять на 2 года данных
- DB: host=10.0.0.64, db=moex, user=postgres, password=postgres
- Таблицы: moex_prices_5m (OHLCV), moex_prices_5m_oi (fiz/yur OI)
- Сохранять результаты в docs/plans/strategy_v2/
- Код → только в trading_bot/new_strategies.py или новые модули trading_bot/strategy_*.py

## Структура кода
```
trading_bot/
├── __init__.py              # конфиги
├── cron_scanner.py          # entry point
├── engine.py                # Volume Surge
├── scanner.py               # data loading
├── tracker.py               # positions
├── new_strategies.py        # OI Divergence, Mean Reversion
├── ob_engine.py             # Order Block
├── reversion_engine.py      # Mean Reversion engine
├── vwap_engine.py           # VWAP engine
├── filters.py               # ADX, ATR
├── strategy_whale.py        # NEW: Whale Detection
├── strategy_spread.py       # NEW: Pair Trading
├── strategy_profile.py      # NEW: Volume Profile
├── strategy_momentum.py     # NEW: Momentum Breakout
├── strategy_ensemble.py     # NEW: Composite pipeline
```

Каждый новый модуль:
- Функция detect_*_signals(symbol, data, config) → list[dict]
- Функция detect_*_signals_limit(symbol, data, config) → list[dict] (огр. на вход)
- Каждый сигнал: {time, ticker, direction, entry, exit, return_pct, strategy}
- exit = entry через horizon баров (как в существующих)
