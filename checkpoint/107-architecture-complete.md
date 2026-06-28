# Checkpoint 107 — Architecture Complete: Engine → Executor → Broker

**Дата:** 2026-06-28
**Проект:** TQA-MOEX-futures
**Предыдущий:** #106 — Portfolio selection

---

## Архитектура

Создана и протестирована 3-слойная архитектура:

```
bar → [Engine] → strategy.check_signal() → Signal → [Executor] → [Broker]
```

### `strategies/common/`
| Файл | Назначение | Статус |
|------|-----------|--------|
| `engine.py` | Портфельный loop по барам, вызывает все стратегии | ✅ |
| `executor.py` | Управление позициями, капиталом, ГО, sizing | ✅ |
| `broker.py` | BrokerSim (работает) + BrokerLive (заглушка) | ✅ |
| `trailing_tp.py` | Параметры 0.5/0.3/12 | ✅ |

### `strategies/*/prod/engine.py` — 4 стратегии
| Стратегия | Сигнал | Файл | Статус |
|-----------|--------|------|--------|
| **Stop Hunt** | Ложные пробои 20-bar + 30% retrace | `stop_hunt/prod/engine.py` | ✅ |
| **CVD** | dcvd_z > 0.6 | `cvd/prod/engine.py` | ✅ |
| **Churn** | OI flat + vol surge | `churn/prod/engine.py` | ✅ |
| **Lunch Reversal** | 13:00 MSK разворот | `lunch_rev/prod/engine.py` | ✅ |

## Тестирование

Stop Hunt на Si через Executor + BrokerSim (Oct'24 — Jun'26):

| Метрика | Значение |
|---------|----------|
| Капитал | 100,000 → 7,248,273 RUB |
| Доходность | **+7,148%** |
| MDD | **1.28%** |
| Calmar | **5,594** |
| Сделок | 4,737 |
| Trailing TP | 77 срабатываний |
| Timeout | 4,660 закрытий |

## Портфель

| Тикер | GO | Контр | ГО | Стратегии |
|-------|:--:|:-----:|:--:|-----------|
| GZ (Газпром) | 2,070 | 5 | 10,350 | StopHunt + CVD + Churn |
| SR (Сбербанк) | 6,620 | 2 | 13,240 | StopHunt + CVD + Churn |
| NG (Natural Gas) | 8,027 | 2 | 16,054 | StopHunt + Churn |
| VB (ВТБ) | 1,556 | 5 | 7,780 | StopHunt + Churn |
| W4 (Пшеница) | 2,255 | 5 | 11,275 | StopHunt + Churn |

Средняя корреляция портфеля: ~0.001

## Файлы

| Файл | Описание |
|------|----------|
| `strategies/common/engine.py` | Портфельный loop |
| `strategies/common/executor.py` | Управление позициями |
| `strategies/common/broker.py` | BrokerSim + BrokerLive |
| `strategies/common/trailing_tp.py` | Параметры |
| `strategies/common/final_test.py` | Smoke test |
| `strategies/stop_hunt/prod/engine.py` | Stop Hunt |
| `strategies/cvd/prod/engine.py` | CVD |
| `strategies/churn/prod/engine.py` | Churn |
| `strategies/lunch_rev/prod/engine.py` | Lunch Reversal |
| `reports/scan_stop_hunt.md` | Stop Hunt scan (36 tk) |
| `reports/scan_cvd.md` | CVD scan (23 tk) |
| `reports/scan_churn.md` | Churn scan (36 tk) |
| `reports/scan_lunch_reversal.md` | Lunch scan (28 tk) |

## Что дальше

1. Портфельный тест всех 4 стратегий на всех тикерах
2. BrokerLive (MOEX API через Alor)
3. Paper trader с реальными данными
