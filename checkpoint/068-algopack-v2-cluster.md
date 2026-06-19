# Checkpoint 2026-06-19: AlgoPack v2 загрузка + кластеризация CH

## Контекст/проблема
Переход с moexalgo SDK на прямой API MOEX AlgoPack v2 (apim.moex.com). Старые скрипты (`scripts/algopack_load.py`) использовали moexalgo и state-файлы, были нестабильны и не поддерживали маппинг колонок `secid→ticker`. Также требовалось настроить ClickHouse на отказоустойчивый кластер.

## Ключевые решения

### 1. Новый скрипт `scripts/algopack_load_v2.py`
- **Прямой HTTP API** — без moexalgo SDK, через urllib.request
- **Автоопределение последней даты** — `SELECT max(tradedate)` из CH вместо state-файлов
- **Batch insert по 1000 строк** — чтобы не перегружать CH
- **Прогресс каждые 60с** — видно сколько строк, % выполнения
- **Маппинг колонок** — `secid` (API) → `ticker` (CH таблица) для tradestats
- **Токен через `T` из env** — берётся из `.env` (`ALGOPACK_APIKEY`)

### 2. ClickHouse кластер
- **MergeTree** вместо ReplicatedMergeTree — ClickHouse Keeper нестабилен (теряет ZNode для новых партиций, ошибка `KEEPER_EXCEPTION: No node`)
- **Local + Distributed схема**:
  - `tradestats_local`, `obstats_local` — MergeTree на каждом ноде
  - `tradestats`, `obstats` — Distributed(`forex_cluster`) с INSERT на оба нода
- **CH слушает на `0.0.0.0:9000`** — через `network.xml` с `listen_try=1`
- **VIP 10.0.0.64** — единая точка входа (настроен на eth0 10.0.0.60)

### 3. Отказоустойчивость
При падении любой ноды:
- Distributed таблица шлёт запросы на живые реплики
- INSERT в упавшую ноду встаёт в очередь
- Если 10.0.0.60 упал — 10.0.0.63 продолжает работать (там тоже Distributed)
- Скрипты конфигурируются через `config.py` → `CH_HOST=10.0.0.64`

## Результаты

| Таблица | Период | Строки | Ошибки |
|---------|--------|--------|--------|
| **tradestats** | 2020-01-01 → 2026-06-18 | 1,720,000 | 0 |
| **obstats** | 2020-01-01 → 2026-06-18 | 1,720,000 | 0 |

Общее время загрузки: ~40 мин × 2 таблицы параллельно.

## Точная конфигурация

### БД
- Сервер: 10.0.0.63:9000 + 10.0.0.60:9000 (кластер `forex_cluster`)
- VIP: 10.0.0.64:9000
- БД: `moex_algopack_v2`
- Таблицы:
  - `tradestats` (Distributed) → `tradestats_local` (MergeTree)
  - `obstats` (Distributed) → `obstats_local` (MergeTree)
- Движок: MergeTree, PARTITION BY toYYYYMM(tradedate)

### Скрипты
- `scripts/algopack_load_v2.py` — основная загрузка
- `config.py` — `CH_HOST=10.0.0.64`, `MOEX_CH_HOST` env override
- `loader.py` — `CH_HOST=10.0.0.64`
- `scripts/algopack_load.py` — `CH_HOST=10.0.0.64` (старый, не используется)

### Токен
- Хранится в `.env`: `ALGOPACK_APIKEY=...`
- Для v2: `T=$(grep ^ALGOPACK_APIKEY .env | cut -d= -f2-)`

### ClickHouse конфигурация
- `/etc/clickhouse-server/config.d/network.xml`:
  ```xml
  <clickhouse>
      <listen_host>0.0.0.0</listen_host>
      <listen_host>::</listen_host>
      <listen_try>1</listen_try>
  </clickhouse>
  ```
- `/etc/clickhouse-server/config.d/cluster.xml` — кластер `forex_cluster` (1 шард, 2 реплики)

## Что не сработало и почему
- **ReplicatedMergeTree через ZooKeeper** — ClickHouse Keeper теряет ZNode при создании новых партиций. Ошибка `KEEPER_EXCEPTION: No node`. Перешли на MergeTree + Distributed.
- **moexalgo SDK** — требует Python 3.12+, state-файлы (не атомарны), нет маппинга `secid→ticker`.
- **mergeTree с `min_rows_for_wide_part = 0`** — не помогло с Keeper проблемой.

## Скрипты
- `scripts/algopack_load_v2.py` — путь в репозитории проекта

## Данные
- API: `https://apim.moex.com/iss/datashop/algopack/eq/{tradestats|obstats}.json?date=YYYY-MM-DD&limit=100000`
- Таблицы CH: `moex_algopack_v2.tradestats`, `moex_algopack_v2.obstats`
- Период: 2020-01-01 → 2026-06-18
- Токен: JWT из `.env` → `ALGOPACK_APIKEY`

## Следующий шаг
Настроить регулярную догрузку свежих данных (через cron). Скрипт `algopack_load_v2.py` сам определяет `max(tradedate)` и загружает только новые дни — можно запускать ежедневно.

## Апдейт (2026-06-19, после push)
- **Проблема**: push таймаутился — в истории было 22 файла `reports/oi_divergence_scan/*_params.json` по 60–100 MB каждый (~2 ГБ)
- **Решение**:
  1. `*_params.json` добавлен в `.gitignore`
  2. `git filter-repo --path reports/oi_divergence_scan/ --invert-paths` — вычистил из всей истории
  3. Force push занял секунды
- **Следствие**: история переписана (коммиты до этого потеряли связь с origin). Те, кто клонировал по HTTPS, должны `git pull --force`.

## Апдейт 2 — orderstats (19.06.2026)
- **Проверка доступных endpoint'ов**: eq + fx (futures/derivatives недоступны)
- **Загружается** `orderstats` (eq) — заявки до сделок (put/cancel), ~2.1K дней
- **Создана таблица** `moex_algopack_v2.orderstats_local` + Distributed (MergeTree, PARTITION BY toYYYYMM, ORDER BY (secid, tradedate, tradetime))
- **Скрипт**: `scripts/orderstats_load.py` (аналог algopack_load_v2.py)
- **Roadmap**: сохранён в Obsidian `Trading/2026-06-19_2155_moex_algopack_roadmap.md`

## Следующий шаг
1. Дождаться загрузки orderstats (~80 мин)
2. Настроить ежедневную догрузку всех 3 датасетов (tradestats, obstats, orderstats)
3. По желанию: загрузить FX наборы (валютный рынок с L2-стаканом)

## Ссылки
- Предыдущий чекпоинт: `067-phase5-final-audit-algopack.md`
- Проект: `~/projects/TQA-MOEX/`
- Репозиторий: origin
