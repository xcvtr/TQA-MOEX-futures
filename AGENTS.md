# Архитектура проекта TQA-MOEX-futures

## 🏗 PG структура

Одна БД `moex` на 10.0.0.60, все данные только в БД:

```
moex
├── futures
│   ├── ticker_specs           ← справочник: ГО, лотность, шаг цены (64 tickers)
│   └── portfolio              ← портфель: тикер × стратегия, параметры, трейлинг
└── shared
    └── calendar               ← макро-календарь (пусто)
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

## 📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ (chkpt 107)

### Архитектура

Создана и протестирована 3-слойная архитектура:

```
bar → [Engine] → strategy.check_signal() → Signal → [Executor] → [Broker]
```

### 4 стратегии × Trailing TP (Si solo)

| Стратегия | WR | NetP80 | Статус |
|-----------|:--:|:------:|:------:|
| **Stop Hunt** | 82% | +7.28% | ✅ **prod** |
| **Lunch Reversal** | 83% | +7.39% | ✅ **prod** |
| **CVD** | 76% | +6.59% | ✅ **prod** |
| **Churn** | 74% | +6.40% | ✅ **prod** |

Si solo (Stop Hunt): 100K → **7,248,273 RUB** (+7,148%), MDD 1.28%, Calmar 5,594.

### 🥇 Портфель (в БД `futures.portfolio`)

| Тикер | GO | Контр | Стратегии |
|-------|:--:|:-----:|-----------|
| GZ (Газпром) | 2,070 | 5 | StopHunt + CVD + Churn |
| SR (Сбербанк) | 6,620 | 2 | StopHunt + CVD + Churn |
| NG (Natural Gas) | 8,027 | 2 | StopHunt + Churn |
| VB (ВТБ) | 1,556 | 5 | StopHunt + Churn |
| W4 (Пшеница) | 2,255 | 5 | StopHunt + Churn |
| Si (USDRUB) | 15,252 | авто | StopHunt + CVD + LunchRev |
| CR (CNYRUB) | — | авто | StopHunt + CVD |

Средняя корреляция портфеля: ~0.001

Параметры стратегий: `futures.portfolio.params` (JSONB)

### 🔑 Ключевые открытия

1. **Trailing TP (0.5/0.3%) — главный edge.** Любой сигнал + трейлинг даёт 75-91% WR.
2. **Stop Hunt — лучший entry.** Ложные пробои 20-барового диапазона + retrace 30%.
3. **Сигнал почти не важен** — трейлинг важнее входа.

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
