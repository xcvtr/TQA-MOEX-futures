# Checkpoint 106 — Portfolio Selection: 4 Strategies × Cross-Ticker Scan

**Дата:** 2026-06-28
**Проект:** TQA-MOEX-futures
**Предыдущий:** #105 — 11 TRIZ ideas tested

---

## 1. Системные сканы завершены

| Стратегия | Сигнал | Прошло | Статус |
|-----------|--------|:------:|:------:|
| **Stop Hunt** | Ложные пробои 20-bar + 30% retrace | **36** ✅ | `reports/scan_stop_hunt.md` |
| **CVD** | dcvd_z > 0.6 | **23** ✅ | `reports/scan_cvd.md` |
| **Churn** | OI flat + volume surge | **36** ✅ | `reports/scan_churn.md` |
| **Lunch Reversal** | 13:00 MSK разворот | **28** ✅ | `reports/scan_lunch_reversal.md` |

Не сканированы: Disb+OI (тяжёлый расчёт), Vol Profile (0 тикеров).

## 2. Параметры тестирования (единые для всех)

- Trailing TP: activation=0.5%, trail=0.3%, timeout=12 bars
- Позиционирование: floor(equity × 0.1 / GO), min 1, max leverage 10×
- Комиссия: 4 RUB/contract, slippage 1 tick
- Старт: 100,000 RUB
- Период: Oct'2024 — Jun'2026

## 3. Портфель (предварительный)

Отбор по: Calmar + MDD < 25% + низкая корреляция между тикерами.

| Тикер | Инструмент | GO | Контр | ГО | Стратегии | Return | MDD | Calmar |
|-------|-----------|:--:|:-----:|:--:|-----------|:-----:|:---:|:------:|
| **GZ** | Газпром | 2,070 | 5 | 10,350 | StopHunt+CVD+Churn | +371% | 14% | 25.7 |
| **SR** | Сбербанк | 6,620 | 2 | 13,240 | StopHunt+CVD+Churn | +167% | 10% | 16.9 |
| **NG** | Nat Gas | 8,027 | 2 | 16,054 | StopHunt+Churn | +492% | 24% | 20.2 |
| **VB** | ВТБ | 1,556 | 5 | 7,780 | StopHunt+Churn | +49% | 5% | 9.3 |
| **W4** | Пшеница | 2,255 | 5 | 11,275 | StopHunt+Churn | +393% | 20% | 20.0 |

**Итого ГО:** 58,699₽ (59% депозита), свободно 41,301₽
**Средняя корреляция портфеля:** ~0.001 (практически нулевая)

**Маппинг asset_code:** GZ=GAZR, SR=SBRF, NG=NG, VB=VTBR, W4=WHEAT

## 4. Ключевые открытия сессии

1. **Trailing TP (0.5/0.3) — главный edge.** Любой сигнал + трейлинг даёт 75-91% WR. Даже CVD с корреляцией −0.0029.
2. **Timeout = 12 баров критичен.** 96 баров убивает стратегию (54% timeout-потерь).
3. **Stop Hunt — лучший entry.** Работает на 36 тикерах. Si: +1,016%, DD 19%.
4. **SR × LK корреляция 0.922** — брать только один.
5. **GZ × W4 = −0.011** — идеальная пара.

## 5. Файлы

| Файл | Описание |
|------|----------|
| `reports/scan_stop_hunt.md` | Stop Hunt — 36 тикеров |
| `reports/scan_cvd.md` | CVD — 23 тикера |
| `reports/scan_churn.md` | Churn — 36 тикеров |
| `reports/scan_lunch_reversal.md` | Lunch Reversal — 28 тикеров |
| `checkpoint/105-triz-ideas-all-tested.md` | TRIZ тестирование |
| `AGENTS.md` | Архитектура проекта |

## 6. Что дальше

1. ✅ Портфель собран
2. 🔜 Обсудить архитектуру тестера (backtest / paper_trader)
3. 🔜 Создать `strategies/stop_hunt/` с engine
4. 🔜 Сохранить портфель в PG
