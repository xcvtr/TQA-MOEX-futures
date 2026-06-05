# 📈 Volume Climax — H4 экстремумы

**Автор:** эвристика (Hermes)
**Таймфрейм:** H4 (основной), 1h (NR/IB/GZ)
**Скрипт:** moex_equity_dashboard.py
**Данные:** moex_prices_5m (Alor) → H4 resample

---

## Идея

Когда объём резко превышает средний, а свеча имеет широкий диапазон и
закрывается у экстремума — рынок перегружен, следующий бар откатывает.

### Условия сигнала

- volume > 2 x rolling_median(20 bars)
- range_pct > avg_range_pct
- close в нижних 35% диапазона → LONG
- close в верхних 35% диапазона → SHORT

### Выход

TP/SL модель: entry на open+0.1% (slippage), TP 0.4%, SL 0.8%, trail, макс 2 бара.

---

## Результаты (close-based, реалистичные)

### H4 — лучшие

| Тикер | WR | PF |
|:------|:--:|:--:|
| SS (SMLT/Sugar) | 78% | 1.91 |
| CC (Cocoa) | 76% | 1.72 |
| SE (SGZH) | 73% | 1.48 |
| PD (PLD) | 73% | 1.58 |
| NG (NG) | 62% | — |
| BR (Brent) | 63-68% | — |

### 1h — для сравнения

| Тикер | Touch-WR% | Сигналов | Сумма PnL% |
|-------|:--------:|:--------:|:----------:|
| CH | 88.4 | 112 | +61.2 |
| W4 | 84.7 | 216 | +70.7 |
| NR | 74.4 | 270 | +32.7 |
| IB | 71.3 | 265 | +42.0 |
| BR | 67.7 | 198 | +21.7 |
| NG | 62.3 | 223 | +67.3 |

**Важно:** touch-WR завышен на 20-35pp. Реалистичный close-based WR: 43-57%.

### H4 vs 1h

| | H4 | 1h |
|:--|:--:|:--:|
| Чемпионов | 41 | 25 |
| Для каких | CH, BM, BR, SV, DX, CC, NG | NR, IB, GZ |

Рекомендация: H4 основной, 1h для NR/IB/GZ.

### GO-эквити (с плечом)

ret_GO = ret_notional x leverage.
Пример: CC (Cocoa), ГО = 473 RUB, плечо = 6.4x. TP 0.4% → +2.6% на ГО.

---

## Авто-отбор чемпионов

- Скрипт: update_champions.py
- Период: еженедельно (cron воск. 08:00)
- Метод: rolling 12-month scan, композитный score
- Лог: champions_history.json

**24 тикера:** BR, BM, CC, CH, DX, GD, GL, GZ, HY, LK, MC, NA, NG, NM, NR, OJ, PD, RI, SE, SN, SR, SS, SV, VB

---

## Ограничения

1. Вечерняя сессия (18:00-20:45 UTC) недоступна исторически
2. Touch-WR ≠ реальность (разрыв 20-35pp)
3. Нет bar-by-bar тестера
4. Не все тикеры имеют ГО на MOEX ISS

---

## Файлы

- moex_equity_dashboard.py — дашборд
- update_champions.py — авто-отбор
- scan_all_tickers.py — полный скан
- check_stability.py — WR по годам
- reports/trading_report_20260603_163919.txt — отчёт
- references/h4-ranking-methodology.md — методология
- references/realistic-backtest-model.md — TP/SL модель
- Дашборд: http://10.0.0.60:5057/
