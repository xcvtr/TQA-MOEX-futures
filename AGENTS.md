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

checkpoint/                     ← чекпойнты (001-108)
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

## 📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ (chkpt 113)

### Портфельный тест (7 тикеров, Янв'25 — Июн'26)

**Параметры:** риск-ориентированный sizing (10% капитала на сделку), комиссия 4 RUB, трейлинг 0.5/0.3/12, стопы по рынку, вход open[i+1] + 1 tick, ликвидность <50%. Данные: PG — 2 мес (43K баров), CH — 18+ мес.

```
Капитал: 100,000 → 200,962 RUB (+101%)
MDD: 23.4% | Сделок: 107 | WR: 64.5% | PF: 1.85 | Период: 18 мес
```

| Тикер | Контр | Стратегии |
|-------|:-----:|-----------|
| GZ (Газпром) | 5 | StopHunt + CVD |
| SR (Сбербанк) | 2 | StopHunt + CVD |
| NG (Natural Gas) | 2 | StopHunt |
| VB (ВТБ) | 5 | StopHunt |
| W4 (Пшеница) | 5 | StopHunt |
| Si (USDRUB) | 1 | StopHunt + CVD |
| CR (CNYRUB) | 1 | StopHunt + CVD |

| Стратегия | Сделок | WR | PnL |
|-----------|:------:|:--:|:----:|
| **Stop Hunt** | 66 | **81.8%** | +1,678,613 ₽ |
| **CVD** | 162 | 54.9% | +438,229 ₽ |

### Ключевые открытия

1. **Stop Hunt — лучшая стратегия.** WR 81.8%, даёт 79% профита портфеля.
2. **Фиксированные контракты** — единственный способ избежать экспоненты.
3. **Churn отключён** — WR 58.6% но отрицательный PnL.
4. **Lunch Reversal отключён** — 8 сделок за 18 мес, слишком редко.
5. **MDD 33.5%** — превышает дефолтный RiskManager (20%). Нужна калибровка.

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
