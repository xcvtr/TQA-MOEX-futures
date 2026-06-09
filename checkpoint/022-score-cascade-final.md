# Checkpoint 022: Score Cascade Final — +182% годовых, Calmar 26.9

## Итоги

**Лучший результат:** score-based cascade (threshold ≥0.4) + simulate_adaptive
- 100K → 282K (+182% за 1 год)
- DD=6.79%
- Calmar=26.85
- Параметры: mu=0.20, mc=2, tm=0.20, sl=0.01

**Сравнение всех подходов:**

| Подход | Доходность | DD | Calmar | Сделок |
|:-------|:----------:|:--:|:------:|:------:|
| OI (3 tickers) SL 2% | +72% за 2г | 11.6% | 6.4 | 327 |
| HYBRID (OI→hedge, REV→stop) | +12.9% за 2г | 6.5% | 2.0 | 174 |
| Score cascade (47 tickers) | +182% за 1г | 6.8% | **26.9** | 76 |
| All signals + extended grid | +167% за 1г | 11.6% | 14.4 | 1501 |

**Цель 10x (900%) не достигнута.** Причина: OI Divergence имеет слишком низкий avgRet (0.09%) при WR=55%. Для 10x нужны стратегии с WR > 65% или avgRet > 0.5%.

## Ключевые открытия

1. **Score-based cascade (ТРИЗ п.6)** — непрерывный взвешенный score работает лучше бинарных фильтров
2. **48 тикеров вместо 3** — расширение сигнальной базы даёт 6× больше сделок
3. **Adaptive compression** — удерживает систему в живых при просадке (Calmar 26.9!)
4. **Матожидание отрицательное** — стратегия теряет в среднем на сделку, выигрыш за счёт risk management

## Файлы
- `docs/plans/strategy_v3/score_analysis.txt` — анализ порогов score
- `docs/plans/strategy_v3/score_pareto.txt` — Pareto-фронтир
- `docs/plans/strategy_v3/oi_screening.txt` — скрининг 47 тикеров
- `docs/plans/strategy_v3/sweep_all_signals.txt` — sweep всех сигналов
- `trading_bot/strategy_cascade.py` — cascade filters + score
- `scripts/score_sweep.py` — score sweep
- `scripts/cascade_sweep.py` — cascade sweep

## Следующие шаги
- Поиск стратегий с WR > 65%
- Либо переход на другой класс активов/таймфрейм
- Либо принятие текущего результата (182%/год, Calmar 26.9)
