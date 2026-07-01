---
title: "Data Infrastructure Cleanup — OI Sources Separation"
checkpoint: 135
date: 2026-07-01
tags: [checkpoint, tqa-moex-futures, data-infrastructure, OI, algopack, iss, cron]
---

# Checkpoint 135 — Data Infrastructure Cleanup: OI Sources Separation

**Дата:** 2026-07-01
**Проект:** TQA-MOEX-futures
**Предыдущий:** #134 — Live 5-min bars

---

## Что было сделано

### 1. Разделение источников OI (futoi)

Раньше ISS и AlgoPack писали fiz/yur OI в одну таблицу `moex.futoi` (CH) и `futures.futoi` (PG). Никакого маркера источника.

Теперь:

| Источник | CH | PG | Особенности |
|---|---|---|---|
| **ISS** (бесплатный, 64 tickers) | `moex.futoi_iss` | `futures.futoi_iss` | С авторизацией — live. Без — >14д. Пишет 2-мес autopurge в PG |
| **AlgoPack** (платный, все 155+ tickers) | `moex.futoi_algopack` | `futures.futoi_algopack` | Всегда live (токен). Пишет 2-мес autopurge в PG |

Старые таблицы (`moex.futoi`, `futures.futoi`) — legacy, новые данные туда не пишутся.

### 2. Починены cron jobs (5 штук)

| Крон | Проблема | Фикс |
|---|---|---|
| MOEX OI Loader (hourly) | script поле было shell-командой вместо пути | Создан `run_moex_oi_loader.sh` |
| MOEX OI silent collector (5мин) | Путь TQA-MOEX не существует | Создан `run_moex_oi_silent.sh` → TQA-MOEX-futures |
| MOEX OI daily update (18:00) | `loader.py 10` → argparse error | Убран аргумент, переведён в no_agent |
| MOEX OI daily incremental (5:00) | `load_eod_oi.py` в archive | Восстановлен, создан `run_moex_eod_oi.sh` |
| MOEX securities/ГО (6:00) | `fetch_securities.py` в archive + хардкод пути | Восстановлен, пути исправлены, создан `run_fetch_securities.sh` |

### 3. Починен AlgoPack loader (`algopack_bars.py`)

| Было | Стало |
|---|---|
| `native=True` (не поддерживается в moexalgo 2.3.2) | `use_dataframe=False` |
| `r.get('tradetime')` | `r.get('ts')` (datetime) |
| `r.get('ticker')` (tradestats) | `r.get('asset_code')` |
| `short_ticker()` (обрезал тикеры) | `asset_code` напрямую |
| Только 7 portfolio tickers | **Все** доступные тикеры (155+) |
| TOM→FUT маппинг: SBER, GAZP, IMOEX | Добавлен `normalize_ticker()` |

### 4. Чистка мусора из `futoi_iss`

Удалено ~12K строк с обрезанными именами от старой `short_ticker()`:
- Single-letter (D, E, H, I, K, L, M, O, P, T, U)
- TOM дубли (CNYRUB, USDRUB, EURRUB, GLDRUB, GLDRUB)

### 5. Spreadsheet refill

Старые данные (9.6M уникальных таймштампов) перенесены из `moex.futoi` в `moex.futoi_iss` + PG аналог.

---

## Состояние данных

```
┌─────────────────────────────┬────────────┬──────────────────────────────┐
│ Таблица (CH)                │ Строк      │ Диапазон                     │
├─────────────────────────────┼────────────┼──────────────────────────────┤
│ moex.futoi_iss (✅ new)     │ 13,934,844 │ 2020-12-25 → сейчас          │
│ moex.futoi_algopack (✅ new)│      1,398 │ 2026-06-30 → сейчас          │
│ moex.futoi (legacy)         │  9,631,148 │ 2020-12-25 → 2026-07-01     │
│ moex.tradestats_fo          │ 21,097,288 │ полная история               │
│ moex.openinterest           │ 18,832,272 │ 2020-12-25 → 2026-06-19     │
│ moex.bars                   │     96,755 │ 2026-06-25 → сейчас          │
└─────────────────────────────┴────────────┴──────────────────────────────┘
```

```
┌─────────────────────────────┬────────────┐
│ Таблица (PG futures)        │ Строк      │
├─────────────────────────────┼────────────┤
│ futoi_iss (✅ new, 2mo)     │    240,719 │
│ futoi_algopack (✅ new, 2mo)│        112 │
│ futoi (legacy, 2mo)         │    240,717 │
└─────────────────────────────┴────────────┘
```

## Cron jobs status

| Крон | Расписание | Статус |
|---|---|---|
| ISS OI Loader (TQA/services/MOEX_LOADER) | hourly, workdays | ✅ no_agent |
| ISS OI silent collector (TQA-MOEX-futures) | every 5min | ✅ no_agent |
| ISS OI daily update | 18:00 daily | ✅ no_agent |
| ISS OI daily incremental (EOD) | 5:00 daily | ✅ no_agent |
| Securities ГО | 6:00 daily | ✅ no_agent (CH write readonly — known issue) |

## Изменённые файлы

| Файл | Изменение |
|---|---|
| `strategies/common/algopack_bars.py` | Fixed moexalgo API, normalized tickers, all tickers enabled, PG write to futoi_algopack |
| `../TQA/services/MOEX_LOADER/loader.py` | — (unchanged, uses old config) |
| `~/.hermes/scripts/run_moex_oi_loader.sh` | New: hourly ISS OI loader |
| `~/.hermes/scripts/run_moex_oi_silent.sh` | New: 5-min ISS OI collector |
| `~/.hermes/scripts/run_moex_eod_oi.sh` | New: EOD ISS OI |
| `~/.hermes/scripts/run_fetch_securities.sh` | New: securities/ГО |
| `~/.hermes/scripts/update_moex_oi.sh` | Fixed: removed positional arg 10 |

## Известные проблемы

1. **Securities ГО CH write readonly** — `moex.securities` на readonly реплике CH. Snapshot сохраняется локально, но в ClickHouse не пишется.
2. **AlgoPack backfill** — `algopack_bars.py --backfill N` работает, но загружает ~1 день/мин. Для полной истории (~2000 дней) нужно ~33 часа.
3. **openinterest** (18.8M) — застрял на 19 июня (EOD крон лежал в archive). Починен, догонит завтра в 5:00.
4. **SEC (MOEX LOGIN)** — ISS запросы используют `passport.moex.com` авторизацию. Если пароль меняется — loader падает на auth.

## Что дальше

1. Запустить AlgoPack backfill для наполнения `futoi_algopack` и `bars` историей
2. Дождаться автоподхвата ISS OI через cron
3. Дождаться завтрашнего EOD обновления `openinterest`
4. Решить проблему с securities ГО → CH
