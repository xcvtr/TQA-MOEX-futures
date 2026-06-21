# MOEX Strategy: Alternate Signals & ML

> **Проблема:** OI + volume score не работает как предсказатель цены на MOEX.
> **Гипотеза:** Другие признаки (LSR, delta volume, ML) могут найти редкий, но сильный сигнал.

## Направление 1: Другие данные

### LSR / Funding (доступны через TQA Bybit API)
- Long-Short Ratio (LSR) — перекос позиций retail
- Funding Rate — стоимость удержания позиции
- Не MOEX, а крипта (Bybit) — если нужен альтернативный рынок

### Delta Volume (доступен из prices_5m)
- `delta_vol = volume * (close > open ? 1 : -1)` — объём с направлением
- `cum_delta = rolling_sum(delta_vol, 20)` — накопленная дельта
- CVD-подобный индикатор из 5m OHLCV

### Price action patterns
- `body_ratio = abs(close-open) / (high-low)` — твёрдость свечи
- `upper_shadow = (high - max(open,close)) / atr`
- `lower_shadow = (min(open,close) - low) / atr`
- Engulfing / Harami / Doji

### Cross-market
- GL (золото) vs USDRUB — корреляция
- Si (доллар/рубль) vs нефть — не в нашей БД

## Направление 2: ML (классификация)

### Постановка задачи
Предсказать: цена вырастет >0.5×ATR за N баров? (бинарная классификация)

### Фичи (из prices_5m + prices_5m_oi)
- oi_r, oi_r_z, oi_accel, fiz_net, yur_net, fiz_yur_delta
- vr, vz, atr_pct, adx
- body_ratio, upper/lower shadow
- close - vwap (если есть)
- hour, weekday
- Лаги: shift(1..5) для всех выше

### Модель
- RandomForest (интерпретируемый)
- XGBoost (сильнее, но чёрный ящик)
- Train/val/test хронологический

### Выход
- feature_importances — какие признаки важны
- precision/recall — quality of signal
- WR на тесте
- Если WR > 55% → backtest с комиссиями

## Процедура

1. Создать `scripts/ml_features.py` — сбор датасета: для каждого тикера собрать все фичи + target (close[i+N]/close[i] > N×)
2. Создать `scripts/ml_train.py` — обучение RF/XGB, feature importance, confusion matrix
3. Создать `scripts/ml_backtest.py` — если ML даёт сигнал → прогон unified simulate с комиссиями
4. Отчёт: лучшие фичи, best model, backtest result

## Данные

- ClickHouse: moex.prices_5m + moex.prices_5m_oi
- Период: 2023-2026 (train 2023-2024, test 2025-2026)
- Тикеры: GL, HS, HY, RN, NM, AF
- Комиссия: 4₽/сделку, slippage 0.1%
