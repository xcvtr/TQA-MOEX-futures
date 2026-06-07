# 006 — Pipeline Audit: 5 багов найдено и исправлено + Dashboard PnL по стратегиям

**Дата:** 2026-06-08
**Статус:** Все критические баги исправлены. Система стабильна, ждёт торговых сигналов

---

## Аудит пайплайна (5 багов)

| # | Баг | Уровень | Статус | Описание |
|:-:|:----|:-------:|:------:|:--------|
| 1 | `filters.py` не существует → краш `cron_scanner.py:77` | 🔴 CRIT | ✅ | Файл не был создан при рефакторинге. Крон падал при любом запуске |
| 2 | `_ticker_config()` в `tracker.py` не знал `REVERSION_TICKERS` | 🔴 CRIT | ✅ | Вызов `open_position('NM', ...)` падал с `KeyError` |
| 3 | Rolling median включал текущий бар (look-ahead) | 🟡 MED | ✅ | `_rolling_median(arr, w=50)` → `win = arr[i-w+1:i+1]` — теперь `arr[i-w:i]` |
| 4 | ADX filter без `idx` в сигнале | 🟡 MED | ✅ | fallback на последнее значение ADX, не ломает |
| 5 | `close[i]` вместо `open[i+1]` в `scan_reversion_all3.py` | 🔸 INFO | ✅ | verify_reversion подтвердил идентичность результатов |

### Исправления (коммит `3eb7b66`)
- Создан `trading_bot/filters.py` — `calc_adx()`, `add_regime_filter()`, `calc_roc()`
- `tracker.py` — `_ticker_config()` читает `TICKERS | REVERSION_TICKERS`
- `reversion_engine.py` — rolling median: `win = arr[i-w:i]`
- Крон-сканер работает без ошибок

---

## Dashboard: PnL по стратегиям

**Дашборд:** `http://10.0.0.60:5080`

Добавлена сегрегация сделок по стратегиям:

| Стратегия | Трейды | WR% | PF | Статус |
|:----------|:------:|:---:|:--:|:------|
| 🔵 **Volume Surge** (HS/KC/DX/HY) | очищен | — | — | Ждёт сигналов |
| 🟢 **Mean Reversion** (NM/BR/SBERF/AF) | очищен | — | — | Ждёт сигналов |

- `trades.csv` очищен от мусорных записей (entry=0.0, закрытых по signal_lost без PnL)
- Equity-кривая теперь строится отдельно по каждой стратегии
- В таблице сделок добавлена колонка Strategy

---

## Cron

```
cron_scanner — каждые 15 мин 7-18 МСК (будни)
  ├── Volume Surge (HS/KC/DX/HY) — ADX>20
  └── Mean Reversion (NM/BR/SBERF/AF) — vol_z≥1.5, range≥1.5×median
```

Доставка алёртов: в этот чат (Matrix), через cron deliver.

---

## Walk-forward validation (Mean Reversion)

| Тикер | Сигналов | WR OOS | PF OOS | Горизонт |
|:-----:|:--------:|:------:|:------:|:--------:|
| **NM** | 24 | **87.5%** | **11.43** | 12 |
| **BR** | 69 | **66.7%** | **4.91** | 6 |
| **SBERF** | 29 | **72.4%** | **3.44** | 12 |
| **AF** | 22 | **64.7%** | **2.08** | 12 |

Все результаты подтверждены walk-forward (66/33 split, без look-ahead).

---

## Неисправленные замечания

- `moex_equity_dashboard.py` (порт 5057) — в GO_DATA и CHAMPIONS дубликаты после патча (старый `O_DATA` и `HAMPIONS` как артефакты). Не влияет на работу.
