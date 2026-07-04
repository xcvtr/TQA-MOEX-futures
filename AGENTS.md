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
    paper_trader.py             ← ✅ универсальный paper trader (Stop Hunt + CVD)
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

## 📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ (chkpt 141 — Stop Hunt COMBINED)

### Портфельный тест (5 тикеров: GZ, Si, RN, GD, CR, Янв'25 — Июн'26)

**Параметры:** 1 контракт, TO=12 bars (60 мин), trailing TP 0.5/0.3, вход open[i+1]+1 tick, комиссия 4 RUB, без future data. Данные: CH `tradestats_fo`.

**Stop Hunt COMBINED (SHORT+LONG):**

| Метрика | **COMBINED** | LONG | SHORT |
|---|---|---|---|
| Trades | **9,109** | 6,083 | 3,026 |
| PnL | **+7,303K** | +6,144K | +1,159K |
| **WR** | **56.4%** | **60.5%** | 48.3% |
| **PF** | **2.03** | **2.22** | 1.57 |

| Ticker | Lot | Trades | PnL | WR |
|---|---|---|---|---|
| GD (GOLD) | 1 | 2,114 | +2,957K | **59.3%** |
| Si | 1000 | 2,386 | +2,687K | 53.9% |
| RN (ROSN) | 100 | 2,157 | +1,106K | **58.3%** |
| GZ (GAZR) | 100 | 2,452 | +554K | 54.9% |
| CR (CNYRUBF) | — | 0 | — | нет данных |

**LONG (60.5% WR, 2.22 PF) > SHORT (48.3%, 1.57).** Оба включены в paper trader.

**Timeout calibration (Stop Hunt):**

| TO | PnL | WR | PF |
|:--:|:---:|:--:|:--:|
| 4 | -25K | 45.8% | 0.99 |
| 8 | +2,336K | 51.0% | 1.48 |
| **12** ✅ | **+3,603K** | **53.4%** | **1.75** |
| 18 | +4,155K | 55.5% | 1.85 |
| 24 | +4,280K | 56.8% | 1.88 |
| ∞ | +4,624K | 59.4% | 1.94 |

### Ключевые открытия

1. **Stop Hunt COMBINED — 56.4% WR, 2.03 PF.** Лучший результат.
2. **LONG (60.5% WR) > SHORT (48.3%).** Но оба дают положительный PnL.
3. **GD (GOLD) — звезда портфеля:** 59.3% WR, +3M за 18 мес.
4. **RN (Роснефть) — отличное дополнение:** 58.3% WR, +1.1M.
5. **Si — стабильный:** 53.9% WR, +2.7M (37% профита).
6. **CR (CNYRUBF) — нет данных** в tradestats_fo. Другой asset_code.
7. **Partial exit убивает** стратегию (3.6M → 45K при 50%@1%).
8. **CVD — на грани шума** (48.7% WR), но оставлен в портфеле.

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
