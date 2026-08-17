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

## 7. 🚨 СКЕПСИС ПЕРЕД РЕПОРТОМ (ОБЯЗАТЕЛЬНО, высший приоритет)

**Любой результат («edge», ROI, WR, z-score) — сначала АТАКОВАТЬ СВОИ ЦИФРЫ, потом репортить.**
Олег проверяет каждый вывод — артефакты вскрываются первым же вопросом.

Чеклист ДО показа пользователю (см. скилл skepticism-before-report):
1. **Каждая нога портфеля ОТДЕЛЬНО** в бэктесте. Портфельный z может быть иллюзией:
   одна нога тянет весь портфель (пример: SPYF+MM z=4.2, но SPYF-only = шум +28%, WR 50.6%).
2. **Selection bias**: нога «найдена» сканом N комбинаций → Бонферрони (z>3.1 при 24 тестах) + OOS.
   Ожидаемо ~1.2 ложных с z>2 из 24.
3. **WR D1 (close-to-close) ≠ бэктест с trailing** — всегда гонять через Backtester фреймворка.
4. **Компаунд без капа = иллюзия разгона** — показывать с капом 2M как live.
5. **Режимность**: >80% PnL из одного года/месяца = режим, не edge. Разбивать по годам.
6. **Контроль**: случайные сигналы должны давать ~50% (бутстрэп p<0.05).
7. **Look-ahead**: WR>70% → подозрение на будущие данные (пример: close Пт текущей недели для сигнала Пн).
8. **Данные**: мультиконтрактность (активный по max OI), TZ-сдвиги, масштаб цен.

Если сомневаешься — сказать «не уверен, проверяю», а НЕ красивую цифру.
