# 🔥 Checkpoint 151 — MTM Curves + Visualize Final

**Дата:** 2026-07-06
**Проект:** TQA-MOEX-futures
**Теги:** mtm, visualize, balance-equity

---

## Что изменилось (после 150)

### Engine: Balance + MTM (mark-to-market)

- `engine.py` — добавлен расчёт floating PnL на каждом баре для открытых позиций
- `executor.py` — `balance_curve` (closed PnL), `mtm_curve` (balance + floating), `mtm_value`
- Floating PnL = для каждой открытой позиции: `(prc - entry) / ms * sp * shares * pct`
- Ранее `eq_curve` отслеживала только баланс (закрытые сделки). Теперь и MTM.

### Visualize: Полный график

`scripts/visualize.py` — 3 панели:
1. **Balance + MTM** — общий equity (синий + оранжевый). Перекрываются при большом капитале.
2. **Floating PnL** (MTM - Balance) — **своя шкала**, видны флуктуации открытых позиций.
3. **Cash Drawdown** — просадка по закрытым сделкам (Balance).
- Downsample до 3000 точек (step ~13) для читаемости.
- Стартовый капитал: 200 000 ₽.
- Ось X: дата/время в MSK.

### Исправления

- `visualize.py` — все ссылки на `mdd_mtm` заменены на `mdd_bal`.
- `gateway.log` — для отправки картинок: check последний `inbound message*Oleg chat=!roomid`.

---

## Результаты (200K, reinvest, 1% risk, 0.7% SL, 18 мес)

```
Balance (closed):  195,823,765 ₽  (+97,812%)
Cash MDD:          1.41%
MTM = Balance:     195,823,765 ₽  (все позиции закрыты)
Сделок:            4,930
WR:                55.2%
PF:                7.206
```

---

## Файлы изменений

```
M strategies/common/engine.py        # floating PnL per bar
M strategies/common/executor.py      # balance_curve, mtm_curve, mtm_value
M scripts/visualize.py               # 3 panels, floating PnL, 200K, downsample

PG: futures.portfolio.contracts = 1 (безопасно для paper trader)
```
