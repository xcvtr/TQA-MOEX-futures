# Checkpoint 020: Adaptive Risk Management — Pareto Optimum

## Что сделано

### 1. total_margin_limit — ограничение суммарной маржи
Добавлен параметр `total_margin_limit` в `scripts/capital_growth_sim.py`:
- Сумма locked_go по ВСЕМ открытым позициям не превышает `capital × total_margin_limit`
- Проверка перед открытием каждой новой позиции

### 2. Adaptive Risk Management (ТРИЗ принцип 15 — Динамичность)
Добавлена `simulate_adaptive()`:
- `compression = current_equity / peak_equity` (cap 1.0, floor 0.3)
- `adaptive_margin_usage = base_margin_usage × compression`
- `adaptive_total_margin = base_total_margin_limit × compression`
- Система сжимает позиции при просадке, расширяет при восстановлении

### 3. Full grid search (1344 комбинации)
- `--sweep` — static risk
- `--sweep-adaptive` — adaptive risk
- `--sweep-dd N` — adaptive risk с фильтром DD≤N%

### 4. Pareto frontier

| DD лимит | Доходность | Факт. DD | Параметры |
|:--------:|:----------:|:--------:|:----------|
| ≤5% | +3% | 1.87% | mu=5%, tm=5%, sl=0.5%, conc=2 |
| **≤10%** | **+57.66%** | **5.43%** | **mu=8%, tm=20%, sl=2%, conc=3** |
| ≤15% | +57.66% | 5.43% | (те же) |

### 5. Verification (4 теста, все PASS)
- stress test: adaptive DD 10.40% vs static DD 12.58% (-2.18pp)
- PnL по 3 сделкам: PASS
- Compression bounds: PASS
- Adaptive vs static: adaptive даёт +57.66%, static даёт -4.40% (те же параметры)

## Оптимальные параметры (в бот)
```
CAPITAL = 300 000
MARGIN_USAGE = 0.08          # 8% на сделку
MAX_CONCURRENT = 3           # макс 3 позиции
MAX_TOTAL_MARGIN = 0.2       # суммарно не более 20% капитала в ГО
STOP_LOSS_PCT = 0.02         # 2% стоп-лосс
MAX_DD_LIMIT = 0.05          # остановка торговли при просадке 5%
```

## Файлы результатов
- `docs/plans/capital_growth/summary_dd10.txt`
- `docs/plans/capital_growth/pareto_dd10_top10.csv`
- `docs/plans/capital_growth/equity_curve_dd10.csv`
- `scripts/verify_adaptive.py`

## Что дальше
1. Применить params в trading_bot/__init__.py
2. Реализовать adaptive risk management в реальном боте
3. Улучшить стратегии — цель: 10x за 1 год при DD≤10%
