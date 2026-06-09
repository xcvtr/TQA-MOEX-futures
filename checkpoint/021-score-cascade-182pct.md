# Checkpoint 021: Score-based Cascade — +182% за 1 год, DD=6.8%, Calmar=26.9

## Что сделано

### 1. TRIZ-анализ бинарных фильтров
Бинарные фильтры (ADX>25, Volume>1.5×) срезают 93% сигналов, но WR поднимают всего на +3pp.
**Решение (ТРИЗ Принцип 6 — Универсальность):** score-based cascade вместо бинарных порогов.

### 2. Score-based cascade
`trading_bot/strategy_cascade.py` — добавлены:
- `compute_quality_score(data, idx)` — 5-компонентный взвешенный score (ADX, Volume, Whale, HVN, ATR)
- `cascade_by_score(base_sigs, data, threshold)` — фильтрация по непрерывному score

Бинарные: ALL 5 filters → 205 sig, WR=61.0%
Score ≥0.3: → 7101 sig, WR=54.9% (но с risk management даёт +182%)

### 3. Скрининг 47 тикеров OI Divergence
Все тикеры с WR>52%. Топ:
- FF: WR=77.2%, avgRet=+2.41%
- AU: WR=73.5%, avgRet=+0.81%
- AF: WR=71.4%, avgRet=+0.14%
- GK: WR=70.6%, avgRet=+0.36%

### 4. Capital Growth Pareto (score cascade + simulate_adaptive)
```
Лучшие: mu=0.20, mc=2, tm=0.20, sl=0.01
→ 100K → 282K (+182%), DD=6.79%, Calmar=26.85
```

## Pareto-фронтир

| DD | Return | Calmar | Params |
|:--:|:------:|:------:|:-------|
| 5% | +124% | 24.9 | mu=15%, conc=2, tm=15%, sl=1% |
| **7%** | **+182%** | **26.9** | **mu=20%, conc=2, tm=20%, sl=1%** |

## Файлы
- `docs/plans/strategy_v3/score_analysis.txt`
- `docs/plans/strategy_v3/score_pareto.txt`
- `docs/plans/strategy_v3/oi_screening.txt`
- `docs/plans/strategy_v3/cascade_analysis.txt`
- `scripts/score_sweep.py`
- `trading_bot/strategy_cascade.py`

## Что дальше
- Добиться 10x (900%+)
- Нужно больше сигналов или выше avgRet
- Возможно: комбинировать score cascade со всеми сигналами + жёсткий стоп 1%
