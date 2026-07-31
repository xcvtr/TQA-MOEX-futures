# RULES.md — TQA-MOEX-futures

## 1. Фреймворк, не монолит

**Запрещено создавать ad-hoc скрипты для тестов.** Вся логика в `strategies/common/`.

Доступные entry points:

```
# Универсальный бэктест (читает PG portfolio)
python strategies/common/backtest.py \
    --tickers GD,GZ,RN \
    --days 365 \
    --risk-pct 2 \
    --tf 60 \              # detect на 60m (ресемпл M1)
    --close-entry           # entry по close сигнального бара

# Без PG портфеля (--strategy override)
python strategies/common/backtest.py \
    --tickers GD,GZ,RN \
    --strategy impulse_return \
    --risk-pct 2 --tf 60 \
    --params '{"impulse_bars":3}'   # 3 бара на 60m = 3 часа

# Параметры стратегии зависят от TF:
#   IR 60m:  impulse_bars=2-3, impulse_pct=0.3
#   IR 5m:   impulse_bars=12 (default)

# Per-ticker TF (--tf-map)
python strategies/common/backtest.py \
    --tickers Si,MM,GZ,RN \
    --risk-pct 2 --capital 200000 \
    --tf-map '{"Si":1,"MM":10,"GZ":5,"RN":1}'

# Backtester класс для программного использования
from strategies.common.backtester import Backtester

# PortfolioEngine + BrokerSim для кастомных тестов
from strategies.common.engine import PortfolioEngine
from strategies.common.broker import BrokerSim
```

**Никаких `_check_*.py`, `test_*.py` в корне проекта.** Тесты — только через `strategies/common/`.

**Detect ≠ Tick:** `--tf N` задаёт детект-таймфрейм. Тик/SL/TP всегда на M1.

## 2. PnL расчёт

```
PnL = (exit - entry) / min_step * step_price - commission  # БЕЗ *lot
```

`lot_volume` не участвует — он уже учтён в `step_price`.

## 3. Detect ≠ Tick

- **Detect** — на ресемпле (3m/5m/10m/15m/30m/60m)
- **Tick/SL/TP** — на M1

## 4. Данные

- `moex.mt5_bars` (M1) — основной источник
- `moex.tradestats_fo` (5-min) — AlgoPack (не все тикеры)
- PG `futures.ticker_specs` — ГО, step_price, min_step

## 5. MOEX часы

- 10:00-18:45 MSK = **15:00-23:45 IRKT**
- Только будни (ПН-ПТ)

## 6. Common pool

Капитал общий (не per-symbol). Risk % от всего капитала на сделку.
GO лимит: `ct = min(ct_risk, ct_go)`
