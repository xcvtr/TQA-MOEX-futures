# ТРИЗ-Rescope: Score-based Cascade вместо бинарных фильтров

## Проблема бинарных фильтров

Каждый фильтр (ADX > 25, Volume > 1.5×) — бинарный порог. Сигнал с ADX=26 проходит, с ADX=24 — нет. Потеря информации, жёсткие границы.

## Решение (ТРИЗ Принцип 6: Универсальность)

Единый score вместо 5 бинарных фильтров:

```python
def compute_score(sig_time, ticker, ohlcv, oi_data) -> dict:
    \"\"\"Вернуть взвешенный score и детали.\"\"\"
    scores = {}
    
    # 1. ADX strength (0..1)
    adx = calc_adx(close, 14)  # на H1
    scores['adx'] = min(adx / 40, 1.0)  # 40 = strong trend
    
    # 2. Volume ratio (0..1+)
    vol_ratio = current_volume / sma_20_volume
    scores['volume'] = min(vol_ratio / 2.0, 1.0)  # 2× average = max
    
    # 3. Whale z-score (0..1)
    yur_z = z_score(yur_buy - yur_sell, 20)
    scores['whale'] = min(abs(yur_z) / 3.0, 1.0)  # 3σ = max
    
    # 4. HVN proximity (0..1)
    dist_to_hvn = min(abs(price - nearest_hvn) / atr, 1.0)
    scores['hvn'] = 1.0 - dist_to_hvn  # closer = better
    
    # 5. ATR calmness (0..1)
    atr_ratio = current_atr / close
    scores['atr'] = 1.0 - min(atr_ratio / 0.03, 1.0)  # <3% = calm
    
    # Weighted total
    weights = {'adx': 0.25, 'volume': 0.20, 'whale': 0.25, 'hvn': 0.15, 'atr': 0.15}
    total = sum(scores[k] * weights[k] for k in weights)
    
    return {'total': total, 'details': scores}
```

**Thresholds:** trade if `score > 0.6` (настраивается)
**Направление:** дополнительно учитывать согласованность со слабыми стратегиями (+0.2 за совпадение)

## Преимущества

1. **Плавная шкала** — можно найти оптимальный threshold по Calmar
2. **Гибкость** — разные веса для LONG/SHORT
3. **Динамичность** — веса меняются по режиму рынка (ТРИЗ 15)
4. **Композит** — слабые сигналы суммируются (ТРИЗ 40)

## Реализация

Добавить в `trading_bot/strategy_cascade.py`:

```python
def compute_quality_score(...) -> dict
def cascade_by_score(signals, ohlcv_data, oi_data, score_threshold=0.6) -> list
def tune_threshold(signals, ohlcv_data, oi_data) -> dict
```
