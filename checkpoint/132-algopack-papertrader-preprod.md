# Checkpoint 132 — AlgoPack API работает. PaperTrader в препроде.

**Дата:** 2026-06-30
**Проект:** TQA-MOEX-futures

---

## Что сделано

### AlgoPack API — заработал!

Ключ `ALGOPACK_APIKEY` валидный. Проблема была в endpoint'е — moexalgo использует `apim.moex.com` (API Management), а не `iss.moex.com`.

**Данные (33 колонки):**
```
pr_open, pr_high, pr_low, pr_close  ← OHLC
vol, vol_b, vol_s                     ← объём (+ buy/sell)
oi_open, oi_close                     ← открытый интерес
disb                                   ← дисбаланс
im                                     ← ГО
trades_b, trades_s                    ← сделки buy/sell
```

### Загрузчик `strategies/common/algopack_bars.py`

- `--load-date YYYY-MM-DD` — загрузка одного дня
- `--backfill N` — загрузка N последних дней
- Пишет в CH `moex.bars` (ReplicatedReplacingMergeTree) + `moex.futoi`
- Пишет в PG `futures.prices` (портфель) + `futures.futoi` (авто purge 2 мес)
- FUTOI тоже через moexalgo (не через ISS bypass)

### Cron

| Джоб | Расписание | Что |
|------|:----------:|-----|
| `load_algopack_bars.sh` | `*/5 10-23 * * 1-5` | AlgoPack бары → CH + PG |
| `run_paper_trader.py` | каждые 5 мин | tick() — только последний бар |
| `load_moex_prices.sh` | `*/5 10-23` | ISS snapshot → PG (fallback) |

### PaperTrader

- `catch_up()` — переписан через Engine (прогоняет всю PG историю с индикаторами)
- `tick()` — проверяет только последний бар (быстро, для cron)
- `use_pg=True` — CH не используется
- `RISK_PCT` = 0.02 (2% на сделку) вместо 0.1

### Данные в PG

- 44K баров (Apr 29 — Jun 29), vol_b/vol_s долиты из CH
- FUTOI: 9.6M строк в CH, 228K в PG

### Найденные проблемы

1. **PG данные не имели vol_b/vol_s** для старого периода — исправлено (43K bars updated)
2. **catch_up() давал 24.9M equity** из-за отсутствия vol_b/vol_s (CVD не работал, Stop Hunt доминировал)
3. **RISK_PCT=0.1** — слишком агрессивно. Снижен до 0.02

---

## Ключевые метрики (Backtester, RISK_PCT=0.02)

```
Период: 29 апр — 29 июн (2 мес)
Капитал: 100K → 2.55M (+2,451%)
MDD: 6.16% | Сделок: 2,245 | WR: 55.4% | PF: 2.73

Stop Hunt: 633 tr, WR 50.2%, PnL +231K
CVD:      1,612 tr, WR 57.4%, PnL +2.22M
```

---

## Файлы

- `strategies/common/algopack_bars.py` — загрузчик AlgoPack
- `strategies/common/paper_trader.py` — catch_up через Engine
- `strategies/common/executor.py` — RISK_PCT=0.02
- `run_paper_trader.py` — только tick()

---

## Cron

| База | Таблица | Данные | Autopurge |
|:----:|---------|--------|:---------:|
| CH | `moex.bars` | OHLCV + vol_b/vol_s/oi | — |
| CH | `moex.futoi` | FIZ/YUR OI | — |
| PG | `futures.prices` | портфель | 2 мес |
| PG | `futures.futoi` | портфель | 2 мес |
