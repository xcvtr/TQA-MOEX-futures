# Checkpoint 119 — Look-ahead fix: entry at open[i+1]. Realistic portfolio.

**Дата:** 2026-06-28
**Проект:** TQA-MOEX-futures
**Предыдущий:** #118 — TRIZ fix

---

## Что сделано

Найден и исправлен **look-ahead bias** во входе — сигнал формировался на close бара,
но вход тоже был на close того же бара. В реальности сигнал известен только после
закрытия, вход — на open следующего бара.

### Баг: сигнал + вход на одном баре

Stop Hunt: `lo[i] < min_lo AND close[i] > lo[i] + 30% range` → вход по `close[i]`
CVD: `dcvd_z[i] > 0.6` → вход по `close[i]`

Проблема: close[i] — это цена, которая подтверждает сигнал. Вы не можете
одновременно подтвердить сигнал И войти по той же цене. Реально вы входите
на open[i+1] после того, как сигнал подтверждён.

### Исправление: pending signals

```
bar[i]:    check_signal() → signal → store as PENDING
bar[i+1]:  execute PENDING at open[i+1] + 1 tick slippage
           check_signal() for bar[i+1] → store as new PENDING
```

### Результат

```
Stop Hunt solo — ДО (вход на close):     100K → 95.9B  (+95M%), WR 80.9%
Stop Hunt solo — ПОСЛЕ (вход на open+1):  100K → 1.13M  (+1,029%), WR 73.0%
```

### Финальный портфель (реалистичный)

```
7 тикеров | Stop Hunt + CVD | 18 мес | Янв'25 — Июн'26
100K → 200,962 RUB (+101%)
107 сделок | WR 64.5% | MDD 23.4% | PF 1.85

Stop Hunt: 29 сделок, WR 69.0%, PnL +25K
CVD:       78 сделок, WR 62.8%, PnL +76K

Параметры:
  sizing:       int(equity * 10% / GO), без капов
  комиссия:     4 RUB round-trip
  трейлинг:     activation=0.5%, trail=0.3%, timeout=12 bars
  стопы:        по рынку (close + slippage), немедленно на том же баре
  вход:         open[i+1] + 1 tick slippage
  ликвидность:  позиция < 50% объёма бара
```

---

## Файл

- `strategies/common/engine.py` — pending signals, entry at open[i+1]

---

## История дня

```
1. Архивация legacy:   7.3GB → ~2MB
2. AGENTS.md:          обновлён
3. config.py:          очищен от мусора
4. Backtester:         создан
5. PaperTrader:        создан
6. RiskManager:        создан
7. Broker:             Position + BrokerSim + BrokerLive stub
8. Executor:           принимает Broker снаружи, load_portfolio()
9. Engine:             pending signals, multi-ticker fix
10. Аудит:             trailing, стопы, slippage, ликвидность, комиссия
11. TRIZ fix:          немедленный стоп, entry slippage 0
12. Look-ahead fix:    entry на open[i+1]

Финальный портфель: 100K → 201K (+101%), WR 64.5%, MDD 23.4%
```

---

## Что дальше

1. BrokerLive — Alor API
2. Оптимизация trailing/z-порогов
3. Docker + копия для прода
