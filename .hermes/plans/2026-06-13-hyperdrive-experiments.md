# HyperDrive: 7 экспериментов для 100%+ CAGR

> **Цель:** Прогнать 7 экспериментов на честном симуляторе с L+S и новым score (oi_accel + fiz_yur_delta).
> **База:** portfolio_sweep_enhancements.py — portf-test с 6 тикерами (GL, HS, HY, RN, NM, AF)

**Контекст:** После аудита выяснилось что `vz.shift(1)` убирает look-ahead. Cargo cult (`clip(1+vz/5)`) убран. Score = `af * (vs*0.3 + os_*0.7)`. Добавлены oi_accel + fiz_yur_delta. Baseline: CAGR ~42%, DD ~9%, Calmar ~4.7.

## План экспериментов

### Эксперимент 1: Lot 50% (удвоение капитала на сделку)
**Файл:** модифицировать `scripts/portfolio_sweep_enhancements.py`
- Найти строку `max_rub = cash * 0.25` → заменить на `max_rub = cash * 0.50`
- Period: 2025-01-01 — 2026-04-30
- Оценить: растёт ли DD пропорционально, или Calmar улучшается

### Эксперимент 2: bars_left=4 (короткие сделки)
- `'bars_left': 13` → `'bars_left': 4`
- Проверить: увеличивается ли частота сделок, не падает ли WR

### Эксперимент 3: bars_left=6 
- `'bars_left': 6`
- Середина между 4 и 8

### Эксперимент 4: M15 таймфрейм (через resample)
- Загрузить 5m данные, ресемпл на 15m: `df.resample('15min').agg(...)`
- Пересчитать все индикаторы на 15m
- bars_left=3 (4×3=12 ~ 8×1.5 на 5m), stop=1.0A

### Эксперимент 5: M15 + supercandles / AlgoPack
- Вместо временного ресемпла — supercandles: бар строится при наборе N тиков или N объёма
- Сравнить с M15 time-based

### Эксперимент 6: M15 + lot 50% + bars=4
- Комбинация лучших параметров

### Эксперимент 7: Комбинация лучшего (финальный конфиг)
- Score с oi_accel + fiz_yur_delta (веса 0.5 и 0.3)
- Оптимальные lot / bars / stop / timeframe
- Фиксация в config.py

## Технические детали

**База:** `scripts/portfolio_sweep_enhancements.py`
- Функция `simulate(df, score_col, start, end, name, sym)` — принимает конфигурацию
- Параметры: lot (через `max_rub = cash * X`), bars_left, stop (atrv * N)
- Score колонка: `'score_conf'`
- Данные: ClickHouse, `moex.prices_5m + moex.prices_5m_oi`

**Новая фича в score (уже в коде):**
```python
d['oi_accel'] = d['oi_r'].diff().rolling(5).mean()
d['fiz_yur_delta'] = (d['fiz_net'] - d['yur_net']).abs() / (d['fiz_net'].abs() + d['yur_net'].abs() + 1)
score = af * (vs*0.3 + os_*0.7 + oi_accel*0.5 + fiz_yur_delta*0.3)
```

**M15 ресемпл:**
```python
def resample_to_15m(df):
    ohlc = df['close'].resample('15min').ohlc()
    vol = df['volume'].resample('15min').sum()
    rez = ohlc.copy()
    rez['volume'] = vol
    # OI данные — последнее значение в окне
    for col in ['fiz_buy','fiz_sell','yur_buy','yur_sell','total_oi']:
        rez[col] = df[col].resample('15min').last().fillna(0)
    return rez.dropna()
```

**Запуск OpenCode:**
```bash
cd /home/user/projects/TQA-MOEX-futures
opencode run 'Выполни план по файлу. Создай скрипт scripts/hyperdrive.py который прогоняет все 7 экспериментов по одному. Каждый эксперимент — свой simulate() с модифицированными параметрами. Результаты — таблица в конце (stdout) + JSON в reports/hyperdrive_results.json. Не меняй portfolio_sweep_enhancements.py — все модификации только внутри нового скрипта.' -f .hermes/plans/2026-06-13-hyperdrive-experiments.md --model opencode/deepseek-v4-flash-free
```

## Проверка
- [ ] 7 экспериментов пройдены
- [ ] Таблица результатов на stdout
- [ ] JSON в reports/hyperdrive_results.json
- [ ] Каждый эксперимент = свой simulate() с параметрами
- [ ] baseline (lot=25%, bars=13, stop=2.0ATR) тоже включён
