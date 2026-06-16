# TF OI Rescue v5 — H1 balance: more trades, keep noroll

## Итоги v4
- noroll: 0% rollover ✅, 86% return ✅, Calmar 4.2 ✅
- Но **2-5 сделок** — мало для WF
- Score-фильтр не работает на H1 → убрать
- ADX не решает → убрать

## Настройка
- **Пороги**: 0.90/1.10 (было 0.85/1.15 — слишком жёстко)
- **min_gap_bars**: 6 (было 12 — слишком долго ждать)
- **No ADX-фильтр**
- **No score-фильтр** (не работает на H1)
- **Noroll обязателен** (rollover убивает стратегию)

## Параметры для теста
Один набор param_config:
```python
{'lookback': 20, 'extreme_window': 10, 'horizon': 12, 
 'bear_threshold': 0.90, 'bull_threshold': 1.10, 'min_gap_bars': 6}
```

Score thresholds: только 0.0 (бесполезен на H1)
Portfolio variants: только noroll

## Критерии
- trades ≥ 15 (для WF нужно >10 сделок)
- rollover% = 0 (noroll)
- Calmar > 2.0
- WF: все 4 фолда прибыльны