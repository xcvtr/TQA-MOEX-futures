# TF OI Rescue v4 — H1 доработка

## Проблемы
1. 78% rollover — позиции не живут, переворачиваются
2. WF — только 1/4 фолдов прибыльна
3. Score-фильтр пропускает 90% на H1

## Изменения

### 1. BarLevelPortfolio — allow_rollover
Файл: `scripts/bar_level_sim.py`
- В `__init__` добавить `self.allow_rollover = True`
- В `run()`, блок `if tk in active:` — обернуть rollover-код в `if self.allow_rollover:`, иначе `continue`

### 2. detect_oi_divergence_signals — tighter thresholds + min_gap_bars
Файл: `trading_bot/new_strategies.py`
- default config: `bear_threshold: 0.85, bull_threshold: 1.15, min_gap_bars: 0`
- ticker cooldown: словарь `last_signal_bar = {}`, проверка `i - last_signal_bar.get(ticker, -min_gap_bars) >= min_gap_bars`

### 3. TF_CONFIGS — H1 только
Файл: `scripts/tf_oi_rescue.py`
```python
'H1': {'resample_rule': '1h', 'params_grid': [
    {'lookback': 20, 'extreme_window': 10, 'horizon': 12, 'bear_threshold': 0.85, 'bull_threshold': 1.15, 'min_gap_bars': 0},
    {'lookback': 20, 'extreme_window': 10, 'horizon': 12, 'bear_threshold': 0.90, 'bull_threshold': 1.10, 'min_gap_bars': 12},
    {'lookback': 20, 'extreme_window': 10, 'horizon': 12, 'bear_threshold': 0.85, 'bull_threshold': 1.15, 'min_gap_bars': 12},
]},
```
H4 и D1 убрать.

### 4. ADX-фильтр
В `detect_scored_signals`: параметр `adx_threshold=0`. Если >0 и adx_value < threshold → skip.

### 5. Portfolio variant: noroll
```python
'noroll': dict(max_concurrent=8, use_score_eviction=False, use_trailing=True, trailing_mult=3.0, max_hold_bars=40, allow_rollover=False),
```

### 6. Запустить тест — только H1
Загрузить данные, запустить portfolio для всех score-фильтров.
Написать отчёт `reports/YYYY-MM-DD-tf-oi-rescue-v4.md`.

## Критерии успеха
- signals/day < 10 (было 70+)
- rollover% < 30% (было 78%)
- WF: все 4 фолда прибыльны
- Calmar > 2.0