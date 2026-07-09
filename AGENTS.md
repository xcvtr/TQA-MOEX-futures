# Архитектура проекта TQA-MOEX-futures

**Последний чекпойнт: 159 (2026-07-10)** — cron portfolio paper trader fix

## 🚨 Правила работы

1. **Линтер обязателен** — после каждого изменения .py/.json/.yaml/.toml проверять синтаксис. Не использовать `patch()` без предварительного чтения файла. `write_file()` и `patch()` авто-линтуют — не подавлять ошибки, не игнорировать warnings.
2. **Дважды проверять перед отчётом** — прежде чем сказать «готово», перепроверить:
   - Файл действительно создан/изменён: `cat`, `ls -la`, `git status`
   - Скрипт запускается без ошибок: прогон dry-run / test run
   - Данные свежие и корректные: прямой SQL/CURL запрос
   - Cron реально работает: `cronjob list` → `last_status == ok`
   - Нет мусора: большие файлы >1MB не попали в git, .env в gitignore
3. **Не гадать** — если результат неочевиден (0 сделок, пустой ответ API), верифицировать через прямой запрос перед докладом.

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
    paper_trader.py             ← ✅ paper trader: modular (load → check → manage)
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

## 📊 ФИНАЛЬНЫЕ РЕЗУЛЬТАТЫ (chkpt 150 — после всех фиксов)

### PnL формула (окончательная)
```python
pnl = (exit - entry) / min_step * step_price * pct - commission
```
**Без `*lot`.** MOEX `STEPPRICE` = ₽ за тик за **контракт** для всех типов (валютные, акционные, товарные).

### Timezone
- CH `tradestats_fo` в Asia/Irkutsk (+08)
- MOEX торгует 10:00-18:45 MSK = 15:00-23:45 IRK
- В engine.py: конвертация IRK→MSK (price_10, hour/minute)
- В backtester.py: фильтр off-hours (05-14 IRK удалены)

### Параметры
| Параметр | Значение |
|----------|---------|
| Trailing TP activation | 0.5% |
| Trailing TP trail | 0.3% |
| Timeout | 12 bars (60 min) |
| Stop loss | 0.7% (hard SL от entry) |
| Commission | 4 RUB round-trip |
| Slippage | 1 tick exit |
| Entry | open[i+1] + 1 tick |
| Risk (reinvest) | 1% капитала на сделку |
| Data | CH tradestats_fo, 18 мес |
| Portfolio | contracts=1 (фикс) / NULL (реинвест) |

### Результат: 1 контракт фикс (PG contracts=1)

**Параметры:** 5 tickers, 1 contract each, 100K капитал, только MOEX часы

| Метрика | Значение |
|---------|---------|
| Финальный капитал | **505,284 ₽** |
| Доходность | **+405%** |
| MDD | **20.40%** |
| Сделок | 517 |
| Win Rate | 70.2% |
| Profit Factor | 2.41 |

| Тикер | Сделок | WR% | PnL | avg PnL |
|-------|:------:|:---:|------:|:-------:|
| Si | 92 | 82.6% | +194,563 ₽ | +2,115 ₽ |
| GZ | 104 | 83.7% | +78,123 ₽ | +751 ₽ |
| RN | 92 | 57.6% | +49,009 ₽ | +533 ₽ |
| CR | 113 | 66.4% | +65,355 ₽ | +578 ₽ |
| GD | 116 | 62.1% | +18,233 ₽ | +157 ₽ |

### Результат: реинвест (PG contracts=NULL)

**Параметры:** 5 tickers, 1% risk, 0.7% SL, 100K капитал, только MOEX часы

| Метрика | Значение |
|---------|---------|
| Финальный капитал | **186,730,724 ₽** |
| Доходность | **+186,631%** |
| MDD | **2.02%** |
| Сделок | 4,992 |
| Win Rate | 55.1% |
| Profit Factor | 7.23 |
| Avg Win | +78,731 ₽ |
| Avg Loss | -13,368 ₽ |

| Тикер | Сделок | WR% | PnL | avg PnL |
|-------|:------:|:---:|------:|:-------:|
| CR | 1,026 | 48.6% | +46,038,657 ₽ | +44,872 ₽ |
| GD | 1,243 | 58.2% | +43,044,185 ₽ | +34,629 ₽ |
| GZ | 807 | 60.2% | +73,513,355 ₽ | +91,095 ₽ |
| RN | 468 | 55.6% | +7,428,374 ₽ | +15,873 ₽ |
| Si | 1,448 | 54.0% | +16,606,153 ₽ | +11,468 ₽ |

### График
Скрипт: `scripts/visualize.py` — equity curve + drawdown

### История фиксов

| Чекпойнт | Что сделано |
|:--------:|:------------|
| 146 | Добавлен `*lot*pct` в формулы — перебор для currency |
| 147 | Убран `*lot`, Si sp 0.001→1.0 — правильно для currency |
| 148 | ✗ step_price ×lot для stock — REVERT (цены per-contract) |
| 149 | Timezone fix IRK→MSK, off-hours filter. Итог: 505K (+405%) |
| 150 | Stop loss 0.7%, risk 1%. Итог: 187M (+187K%), MDD 2.02% |

### ⏸ Прочее
- **CVD:** на грани шума (48.7% WR), отключён
- **Churn, Lunch Reversal:** отключены
- **Engine._pending:** list (поддерживает несколько стратегий на тикер)
- **PG host:** 10.0.0.60 (CH + PG)

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
