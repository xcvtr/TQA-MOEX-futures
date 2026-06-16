# Checkpoint 063: MOEX OI Loader — ClickHouse + PostgreSQL fix

**Дата:** 2026-06-16 (IRKT, +08:00)

## Что сделано

### 1. MOEX ISS — найден байпас free-user ограничения
- Суффикс `d` в конце `till` (например `till=2026-05-31d`) + даты >14 дней давности снимают блокировку MOEX
- MOEX игнорирует указанные даты и отдаёт последние 1000 снапшотов
- Авторизация через passport.moex.com работает, но необязательна

### 2. Создан `loader_ch.py` — двойная запись (CH + PG)
- Путь: `/home/user/projects/TQA/services/MOEX_LOADER/loader_ch.py`
- **ClickHouse:** `moex.openinterest` — 64 тикера, 18.7M записей
- **PostgreSQL:** `10.0.0.63` → `openinterest_moex` (primary, реплицируется на 127.0.0.1 и 10.0.0.60)
- Если PG падает — пишет только в CH, без падения
- Использует bypass-даты `2020-01-03..2020-01-10d`

### 3. Починены старые loader-зависимости
- `config.py` — DB_HOST по умолчанию `127.0.0.1` (standby)
- В `loader_ch.py` — отдельный `PG_HOST=10.0.0.63` для записи на primary
- Таблица `openinterest_moex` создана на primary и разошлась на реплики

### 4. Починен `price_loader.py` (MOEX Price Snapshot)
- MOEX изменил формат `marketdata.columns` — теперь список строк, а не список словарей
- Фикс: `cols = data["marketdata"]["columns"]` вместо `[c["name"] for c in ...]`
- Пишет в ClickHouse `moex.prices`

### 5. Крон OI Loader
- **Расписание:** `*/5 15-23,0-5 * * 1-5` (IRKT = 10:00–05:00 МСК, по будням)
- Каждые 5 минут — забирает последние снапшоты всех 64 тикеров
- ReplacingMergeTree дедуплицирует по (symbol, time, clgroup)
- В отчёте — только реально новые строки (по `created_at`)

### 6. Состояние кластера PG
| Хост | Роль |
|------|------|
| **10.0.0.63** | **Primary** (пишет) |
| 10.0.0.60 | Standby |
| 127.0.0.1 | Standby |

## Ключевые файлы
- `services/MOEX_LOADER/loader_ch.py` — новый loader (CH + PG)
- `TQA-MOEX/price_loader.py` — починенный price snapshot
- Крон OI: `job_id=f11c3ff3280f`, имя "MOEX OI Loader"

## Последний снапшот в данных
- CH `moex.openinterest` — **2026-06-15 23:50:00**
- PG `openinterest_moex` — **2026-06-15 23:50:00**
