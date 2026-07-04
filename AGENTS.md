# Архитектура проекта TQA-MOEX-futures

## 🏗 Структура данных

### ClickHouse (10.0.0.64:8123, db=moex)

```
moex
├── futoi_iss              ← ISS OI fiz/yur (13.9M rows, интрадей, 64 tickers)
├── futoi_algopack         ← AlgoPack OI fiz/yur (1.4K rows, все tickers)
├── tradestats_fo          ← AlgoPack OHLCV+vol_b/vol_s+oi (21M rows)
├── openinterest           ← ISS EOD OI fiz/yur (18.8M rows, до 2026-06-19)
├── bars                   ← 5-min bars (97K rows, c 2026-06-25)
├── prices_5min            ← Цены из portfolio loader
├── securities             ← ГО/лот/шаг (29 rows)
└── futoi                  ← ⛔ legacy (9.6M, не пишется)
```

### PostgreSQL (10.0.0.64:5432, db=moex, schema futures)

```
futures
├── futoi_iss              ← ISS OI fiz/yur (241K rows, 2 мес autopurge)
├── futoi_algopack         ← AlgoPack OI fiz/yur (112 rows, 2 мес autopurge)
├── prices                 ← 5-min цены портфеля (46K rows, 2 мес)
├── portfolio              ← Конфиг портфеля (17 rows)
├── paper_state            ← Состояние paper trader
├── ticker_specs           ← Справочник: ГО, лот, шаг (64 tickers)
└── futoi                  ← ⛔ legacy (241K, не пишется)
```

## 📁 Структура проекта

```
strategies/
  common/                       ← общая архитектура (Engine → Executor → Broker)
    engine.py                   ← портфельный loop по барам
    executor.py                 ← управление позициями, капиталом, ГО
    broker.py                   ← BrokerSim + BrokerLive (заглушка)
    trailing_tp.py              ← параметры 0.5/0.3/12 bars

  stop_hunt/                    ← Stop Hunt (ложные пробои) ✅ prod
    prod/engine.py
    dev/

  cvd/                          ← CVD (dcvd_z) ✅ prod
    prod/engine.py, lib.py
    dev/

  churn/                        ← Churn (OI flat + vol surge) ✅ prod
    prod/engine.py
    dev/

  lunch_rev/                    ← Lunch Reversal (13:00 MSK) ✅ prod
    prod/engine.py
    dev/

checkpoint/                     ← чекпойнты (001-135+)
reports/                        ← отчёты сканирования
```

## 🗄 Принципы хранения данных

1. **Всё в БД** — конфиги, портфель, состояние. Никаких JSON/YAML на диске.
2. **`futures.ticker_specs`** — справочник (ГО, лот, шаг цены)
3. **`futures.portfolio`** — портфель: какие стратегии на каких тикерах, параметры трейлинга
4. **Trailing TP (0.5%/0.3%) — основной выход**
5. **Схема = рынок** (futures), а не стратегия
6. **Новая стратегия = `strategies/xxx/`**, не трогает старый код

---

## 📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ (chkpt 137 — после чистки портфеля)

### Портфельный тест (6 тикеров после удаления VB, SR, Янв'25 — Июн'26)

**Параметры:** 1 контракт, TO=12 bars (60 мин), trailing TP 0.5/0.3, вход open[i+1]+1 tick, комиссия 4 RUB, без future data. Данные: CH `tradestats_fo`.

**Stop Hunt ONLY (без CVD):**

| Метрика | Значение |
|---|---|
| Trades | 7,785 |
| WR | **53.5%** |
| PF | **1.75** |
| PnL | +3,618K |

| Тикер | Стратегии |
|-------|:---------:|
| GZ (Газпром) | StopHunt + CVD |
| NG | StopHunt |
| Si (USDRUB) | StopHunt + CVD |
| W4 (Пшеница) | StopHunt |
| CR (CNYRUBF) | StopHunt + CVD (нет данных) |

**Timeout calibration (Stop Hunt):**

| TO | Time | PnL | WR | PF |
|:--:|:----:|----:|:--:|:--:|
| 4 | 20m | -150K | 43.7% | 0.97 |
| **12** ✅ | **60m** | **+3,623K** | **50.7%** | **1.65** |
| 18 | 90m | +4,173K | 52.8% | 1.74 |
| 24 | 120m | +4,301K | 54.1% | 1.76 |
| ∞ | inf | +4,644K | 57.8% | 1.82 |

### Ключевые открытия

1. **Stop Hunt — работает.** 53.5% WR, 1.75 PF — реалистичный edge
2. **CVD — на грани шума.** 48.7% WR, почти нулевой PnL
3. **Partial exit убивает** Stop Hunt (PnL становится отрицательным)
4. **VB, SR удалены** из портфеля — приносили -35K суммарно
5. **Si делает 73% профита** из-за высокой стоимости пункта (1000 RUB/pt)

### 📁 Файлы результатов

| Файл | Описание |
|------|----------|
| `reports/scan_stop_hunt.md` | Stop Hunt scan (36 tk) |
| `reports/scan_cvd.md` | CVD scan (23 tk) |
| `reports/scan_churn.md` | Churn scan (36 tk) |
| `reports/scan_lunch_reversal.md` | Lunch scan (28 tk) |
| `reports/scan_vol_profile.md` | Vol Profile scan |
| `checkpoint/105-triz-ideas-all-tested.md` | TRIZ анализ 10 идей |

---

## 🔜 Что дальше

1. **Engine + portfolio из PG** — читать `futures.portfolio`, не хардкодить
2. **BrokerLive** — подключение к Alor API
3. **Расширение портфеля** — больше тикеров, веса
4. **Докер + копия для прода**

## ⚠️ Force push

Если не работает `git pull`:
```
git fetch --force && git reset --hard origin/main
```

---

## 📝 Как делать чекпойнт (обязательно)

При слове «checkpoint» / «чекпойнт» / «сохрани чекпойнт»:

1. Загрузить **`skill_view(name='checkpoint')`** — скилл в `general/checkpoint`, pinned
2. `session_search(query="checkpoint", limit=3)` — узнать последний номер NNN
3. Определить проект по месту разговора (не путать проекты!)
4. Собрать содержимое: что изменилось, ключевые метрики, таблицы в ASCII box-drawing
5. Сохранить в ДВА места:
   - Проект: `<project>/checkpoint/<NNN>-desc.md`
   - Obsidian: `~/obsidian/Projects/<project-name>/<NNN>-desc.md`
6. Обновить CHANGELOG.md, AGENTS.md, README.md
7. `git add <файлы>` + `git commit` + `git push`
8. Уведомление в Matrix (в канал проекта, не путать комнаты!)

**Важно:**
- Не `git add -A` — только файлы чекпойнта
- Не путать номер NNN между проектами
- Проверить большие файлы (>1MB) перед commit
- Проверить `.env` в gitignore
- Полный цикл: чекпойнт → Obsidian → CHANGELOG → git commit/push
- Если не можешь отправить в Matrix — не отправляй, просто закоммить
