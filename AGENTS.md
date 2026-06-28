# Архитектура проекта TQA-MOEX-futures

## 🏗 PG структура

Одна БД `moex` на 10.0.0.60, схемы по типам инструментов:

```
moex
├── futures                    ← фьючерсы (наш проект)
│   ├── ticker_specs           ← ГО, лотность, шаг цены (64 tickers)
│   └── strategy_cvd_*         ← CVD (legacy, не используется)
├── shared
│   └── calendar               ← макро-календарь (пусто)
├── stocks / options / bonds   ← когда появятся
└── public (пусто)
```

## 📁 Структура проекта

```
strategies/
  cvd/                          ← CVD (закрыто — no edge без трейлинга)
    prod/engine.py, lib.py, paper_trader.py
    dev/
    scripts/ (analyze_tpsl, scan, wf_*)

  stop_hunt/                    ← следующая стратегия (не создана)
  lunch_reversal/               ← следующая (не создана)

scripts/                        ← общие утилиты
configs/                        ← бэкап конфигов
checkpoint/                     ← чекпойнты (001-105)
```

## 🧠 Принципы

1. **Схема = рынок** (futures), а не стратегия
2. **Новая стратегия = `strategies/xxx/`**, новый код не трогает старый
3. **Engine immutable** — эксперименты в dev/
4. **PG — единый источник конфигов**, хардкода нет
5. **Trailing TP (0.5%/0.3%) — основной выход**, а не фиксированный TP/SL

---

## 📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ (chkpt 105)

### 11 сигналов × Trailing TP

Все протестированы на `moex.tradestats_fo`, Oct'24 — Jun'26, с Trailing TP (activation=0.5%, trail=0.3%, max 96 bars).

| # | Сигнал | Тикеры | WR | NetP80 | Статус |
|---|--------|--------|:--:|:------:|:------:|
| 1 | **Stop Hunt** (ложные пробои) | GZ/Si/CR | 91/82/82% | +11/+7/+7 | ✅ **Внедрить** |
| 2 | **Lunch Reversal** (13-14 MSK) | GZ/Si | 87/83% | +10.6/+7.4 | ✅ **Внедрить** |
| 3 | **CVD** (dcvd_z>0.6) | GZ/Si/CR | 84/76/74% | +9/+6.6/+6 | ✅ **Внедрить** |
| 4 | Churn (OI flat+vol) | GZ/Si/CR | 85/74/74% | +9.3/+6.4/+5.5 | 🟡 Опционально |
| 5 | Vol Profile S/R | GZ/Si/CR | 86/83/87% | +8.1/+6.5/+6.3 | 🟡 Опционально |
| 6 | Disb_z+OI_z Combo | GZ/Si/CR | 72/70/66% | +1.2/+2.4/+1.9 | 🟡 Слабый |
| 7 | OI Spike new_shorts | GZ/Si/CR | 64/58/56% | −1.2/−1.7/−1.4 | ❌ |
| 8 | Cross-ticker Si/CR | Si/CR | 59% | — | ❌ Слабый |
| 9 | FIZ/YUR Divergence | все | ~50% | — | ❌ Нет edge |
| 10 | HHI + Price Div | — | слишком редко | — | ❌ |
| 11 | Session: Open Drive | все | 37-50% | — | ❌ Антисигнал |

### 🥇 Портфель (8 стратегий)

| № | Тикер | Сигнал | WR | NetP80 | Вес |
|---|-------|--------|:--:|:------:|:---:|
| 1 | **GZ** | **Stop Hunt** | **91%** | **+10.95** | 15% |
| 2 | **GZ** | **Lunch Reversal** | 87% | +10.59 | 15% |
| 3 | **GZ** | **CVD** | 84% | +9.04 | 15% |
| 4 | **Si** | **Stop Hunt** | 82% | +7.28 | 15% |
| 5 | **Si** | **Lunch Reversal** | 83% | +7.39 | 10% |
| 6 | **Si** | **CVD** | 76% | +6.59 | 10% |
| 7 | **CR** | **Stop Hunt** | 82% | +6.95 | 10% |
| 8 | **CR** | **CVD** | 74% | +6.01 | 10% |

### 🔑 Ключевые открытия

1. **Trailing TP (0.5/0.3%) — главный edge.** Любой сигнал + трейлинг даёт 75-91% WR. Даже CVD с корреляцией −0.0029.
2. **Stop Hunt — лучший entry.** Ложные пробои 20-барового диапазона + retrace 30%.
3. **Сигнал почти не важен** — трейлинг важнее входа.

### 📁 Файлы результатов

| Файл | Описание |
|------|----------|
| `reports/triz_moex_futures_analysis.md` | TRIZ анализ 10 идей |
| `/home/user/test_all_7_signals.py` | 7 сигналов × Trailing TP |
| `/home/user/all_7_signals_results.csv` | Полные результаты |
| `/home/user/stop_hunt_scenarios.py` | Тест сессионных фильтров |
| `checkpoint/105-triz-ideas-all-tested.md` | Чекпойнт |

### 🔜 Что дальше

1. Создать `strategies/stop_hunt/` с engine + paper_trader
2. Создать `strategies/lunch_reversal/` 
3. Сохранить параметры портфеля в PG
4. Добавить Trailing TP как общий модуль

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
