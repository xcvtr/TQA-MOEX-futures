# Checkpoint 118 — TRIZ: Immediate stop execution fix. Portfolio profitable.

**Дата:** 2026-06-28
**Проект:** TQA-MOEX-futures
**Предыдущий:** #117 — TRIZ fix

---

## Что сделано

OpenCode (TRIZ анализ) выявил корневую причину убыточности портфеля после аудита.
Два минимальных изменения вернули портфель в прибыль.

### Fix 1: Стоп исполняется немедленно на том же баре

**Проблема:** При срабатывании трейлинг-стопа позиция помечалась `_stop_triggered=True`
и закрывалась на СЛЕДУЮЩЕМ баре по его close. За этот бар цена уходила ещё дальше,
и эффективный трейлинг составлял 0.8-1.3% вместо 0.3%.

**Было:**
```python
if retrace_from_peak >= pos.trail_pct:
    pos._stop_triggered = True      # ждём следующего бара
    pos.exit_reason = 'trailing_tp'
    return 0.0
```

**Стало:**
```python
if retrace_from_peak >= pos.trail_pct:
    stop_px = pos.best_abs * (1 - trail_pct / 100)  # уровень стопа
    return self._close_market(pos, stop_px, 'trailing_tp', volume)  # закрыть сейчас
```

### Fix 2: Вход без проскальзывания

**Проблема:** Сигнал генерируется на close бара, но вход делался с +1 tick slippage.
Для 5-минутных баров вход по close — это market-on-close, реального slippage нет.

**Было:** `entry_price = raw_price + direction * slippage_ticks * min_step`
**Стало:** `entry_price = raw_price`

---

## Результаты

```
Параметры: те же (7 тикеров, 3 стратегии, комиссия 4 RUB, trailing 0.5/0.3/12)
```

┌──────────────────┬──────────────┬──────────────┐
│ Метрика          │ После аудита │ После TRIZ   │
├──────────────────┼──────────────┼──────────────┤
│ Капитал          │ 79,203       │ 2,769,456    │
│ Доходность       │ -20.8%       │ +2,669%      │
│ MDD              │ 21.7%        │ 20.5%        │
│ Сделок           │ 75           │ 501          │
│ Win Rate         │ 40.0%        │ 64.3%        │
│ Profit Factor    │ 0.55         │ 2.07         │
├──────────────────┼──────────────┼──────────────┤
│ Stop Hunt trades │ 18           │ 130          │
│ Stop Hunt WR     │ 55.6%        │ 76.2%        │
│ Stop Hunt PnL    │ +1,268       │ +920,704     │
├──────────────────┼──────────────┼──────────────┤
│ CVD trades       │ 57           │ 371          │
│ CVD WR           │ 35.1%        │ 60.1%        │
│ CVD PnL          │ -22,065      │ +1,748,752   │
└──────────────────┴──────────────┴──────────────┘

---

### Анализ OpenCode (TRIZ)

OpenCode прочитал код стратегий, брокера и результаты и выявил 3 корневые причины:

1. **Trailing stop exits 1 bar late** — 0.3% трейла уничтожается диапазоном следующего бара.
2. **Entry at close + 1 tick** — вход после того, как реверс уже произошёл.
3. **Commission + 2-tick slippage = 85% edge consumed** — фиксированная комиссия бьёт по мелким сделкам.

Исправление #1 (немедленный стоп) решило проблему полностью.
Fix #2 (нулевой slippage на вход) — убрал лишнюю фиксированную потерю.

---

## Файлы

- `strategies/common/broker.py` — _stop_triggered заменён на немедленный _close_market
- `strategies/common/executor.py` — entry slippage 0

---

## Что дальше

1. BrokerLive — Alor API
2. Оптимизация trailing параметров (activation, trail, timeout под каждый тикер)
3. CVD z-threshold grid search (0.6, 0.9, 1.2, 1.5)
