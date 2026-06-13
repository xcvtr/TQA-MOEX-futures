# TRIZ 300% Phase 3 — Chandelier Exit + Score Sizing + Partial Exit

## Цель: поднять портфель с 72% до 300%+ годовых через 3 изменения в бэктесте

## Изменение 1: Trailing stop (chandelier ATR-based exit)

**Текущий код** (в diamond_search.py, backtest_signal, ~строка 200):
```python
sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
stop_hit = False
xp = float(close[xi])
if sl_pct > 0:
    for j in range(ei, xi + 1):
        if float(low[j]) <= sp:
            xp = sp; stop_hit = True; break
```

**Новый код:**
```python
# Chandelier exit: trailing stop at ATR_mult * ATR from running high
# ATR_mult=3.0 (default), min_stop=1%, max_loss=5%
if use_chandelier:
    running_high = ep  # highest since entry
    trail_pct = max(atr[i] / ep * 3.0, 0.01)  # ATR*3 or 1% whatever is larger
    sp = ep * (1 - min(trail_pct, 0.05))  # max 5% total loss
    xp = float(close[xi])
    for j in range(ei, xi + 1):
        bar_high = float(high[j])
        if bar_high > running_high:
            running_high = bar_high
            # Recalculate trail from new high
            new_trail = max(atr[j] / running_high * 3.0, 0.01) if j < len(atr) else 0.01
            sp = max(sp, running_high * (1 - min(new_trail, 0.05)))
        if float(low[j]) <= sp:
            xp = sp; stop_hit = True; break
else:
    # Original fixed SL
    sp = ep * (1 - sl_pct) if sl_pct > 0 else 0
    xp = float(close[xi])
    if sl_pct > 0:
        for j in range(ei, xi + 1):
            if float(low[j]) <= sp:
                xp = sp; stop_hit = True; break
```

## Изменение 2: Partial exit (50% at 0.5×ATR from entry)

После входа в позицию, если цена прошла 0.5×ATR в нашу сторону — закрыть 50% позиции, остальное до trailing stop.

```python
# После расчёта nc (числа контрактов), перед циклом по барам:
first_half_closed = False
nc_initial = nc

# Внутри цикла по барам, после проверки chandelier stop:
if use_partial_exit and not first_half_closed:
    bar_high = float(high[j])
    target = ep + (atr[ei] * 0.5)  # 0.5*ATR вверх (LONG)
    if bar_high >= target:
        half = nc // 2
        if half > 0:
            half_pnl = half * cs * (target - ep)
            eq += half_pnl - half * COMM // 2
            nc = nc - half
            first_half_closed = True
            trades.append(dict(..., partial=True, half_pnl=round(half_pnl,0)))
```

## Изменение 3: Score sizing — распределение капитала от Calmar

В портфельном симуляторе, вместо равного распределения:
```python
# Вместо: sig_cap = capital / len(signals)
# Новое:
total_calmar = sum(max(sig['calmar'], 0.1) for sig in signals_with_metrics)
sig_weights = [max(s['calmar'], 0.1) / total_calmar for s in signals_with_metrics]
# Для каждой сделки:
sig_cap = capital * weight
```

## Изменение 4: Score-based eviction (rolling window)

Если последние 5 сделок по тикеру дали net negative PnL — уменьшить вес вдвое. Если последние 3 дали 100% убыток — исключить до следующего сигнала.

## Аудит на каждом шагу

После каждого изменения перезапустить портфель и сравнить с baseline (V2: 168% за 2 года).

### Audit check 1: Chandelier exit
- Запустить: reports/triz_diamond_v4/audit_chandelier.py (создать)
- Сравнить: количество сделок, WR, DD, Calmar
- Ожидание: WR ↑ на 5-10%, DD ↑ не более чем в 1.5x

### Audit check 2: Partial exit
- Добавить partial exit
- Ожидание: Calmar ↑ за счёт фиксации прибыли

### Audit check 3: Score sizing
- Распределение капитала по Calmar
- Ожидание: общая доходность ↑, DD = тот же или ниже

### Audit check 4: Все вместе
- Полный портфель с 4 изменениями
- Ожидание: >300% годовых, DD < 10%
