# Strategy Cascade: OI Divergence + Filters → 70%+ WR, 10x capital

## Философия

OI Divergence имеет объективный edge (56% WR, avg +7.92%). Проблема не в стратегии, а в качестве сигналов. 

**Решение:** не новые стратегии, а каскад фильтров поверх OI Divergence.

```
Сигнал OI Divergence →
  ├─ Фильтр 1: ADX (только тренд)
  ├─ Фильтр 2: Volume Confirmation
  ├─ Фильтр 3: Whale Coincidence (yur тоже двигается)
  ├─ Фильтр 4: Support/Resistance (цена у HVN)
  └─ Фильтр 5: Volatility (ATR не экстремальный)
     ↓
  Торгуем только если прошёл ВСЕ
```

Каждый фильтр повышает WR, снижая количество сигналов. Цель: WR > 70% при trade count 200-500/год.

## Требования к коду

Создать модуль `trading_bot/strategy_cascade.py`:

```python
# cascade filters — apply on top of any base strategy
def adx_filter(symbol, data, threshold=25): ...
def volume_filter(data, vol_mult=1.5): ...
def whale_coincidence_filter(oi_data, z_thresh=1.5): ...
def hvn_filter(data, lookback=20): ...
def atr_filter(data, max_atr_pct=0.02): ...

# Main cascade
def detect_cascade_signals(symbol, base_sigs, ohlcv, oi_data, config):
    """Apply all filters to base signals. Return only those passing ALL."""
    ...
```

## Рабочий план

### Фаза 1 (30 мин): Исследование OI Divergence

Найти все тикеры, где OI Divergence даёт WR > 52%:
```
SELECT DISTINCT symbol FROM moex_prices_5m_oi
```
Проверить каждый тикер:
- WR за 2 года
- Средний return
- Оптимальный horizon (6, 12, 24 баров)
- ADX и ATR характеристики
- Сохранить в `docs/plans/strategy_v3/oi_screening.txt`

### Фаза 2 (1 час): Cascade Filters

Реализовать 5 фильтров в `trading_bot/strategy_cascade.py`:
1. **ADX Filter** — H1 ADX(14) > threshold (по умолчанию 25)
2. **Volume Surge Confirmation** — volume > 1.5× average(20) в момент сигнала
3. **Whale Coincidence** — yur_buy/sell z-score > 1.5 (крупные игроки тоже входят)
4. **HVN Support/Resistance** — цена находится у High Volume Node (предзагруженный Volume Profile)
5. **ATR Cap** — ATR(14) < 2% от цены (не торгуем в экстремальную волатильность)

Каждый фильтр — отдельная функция. Можно комбинировать любую комбинацию.

### Фаза 3 (2 часа): Cascade + OI Divergence sweep

Добавить `detect_cascade_signals()` в `scripts/capital_growth_sim.py`:
- Собирает OI Divergence сигналы
- Применяет каждый фильтр
- Считает WR на каждом уровне фильтрации
- Сохраняет отчёт: сколько сигналов отсеял каждый фильтр, какой WR после каждого

Пример вывода:
```
OI Divergence raw:       1111 sig, WR=56.0%, avgRet=+7.92%
├─ +ADX filter:          892 sig (отсеял 219), WR=58.2%
├─ +Volume filter:       445 sig (отсеял 447), WR=62.5%
├─ +Whale filter:        223 sig (отсеял 222), WR=67.3%
├─ +HVN filter:          112 sig (отсеял 111), WR=71.4%
└─ ALL filters:          55 sig, WR=78.2%, avgRet=+12.4%
```

### Фаза 4 (2 часа): Полный sweep с cascade

Запустить `--sweep-cascade`:
- base_margin_usage: [0.05, 0.08, 0.10, 0.15, 0.20]
- max_concurrent: [2, 3, 5]
- base_total_margin_limit: [0.10, 0.15, 0.20, 0.30]
- filters: all combinations (1 filter, 2 filters, ..., 5 filters)
Цель: +900% за 1 год при DD ≤ 15%

### Фаза 5 (30 мин): Верификация

Для лучшей комбинации:
- Проверить 3 сделки вручную (PnL по формуле)
- Сравнить adaptive cascade vs обычный OI Div
- Проверить look-ahead bias
- Сохранить в `docs/plans/strategy_v3/crosscheck.txt`

## Критические правила
- NO look-ahead bias — все фильтры только на исторических данных
- ADX, ATR — rolling window, только прошлые данные
- Volume Profile — строится на барах ДО сигнала
- Сохранять промежуточные результаты после каждой фазы
