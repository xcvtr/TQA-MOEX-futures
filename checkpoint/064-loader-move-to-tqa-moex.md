# Checkpoint 064: MOEX OI Loader — перенос в TQA-MOEX

**Дата:** 2026-06-16 (IRKT, +08:00)

## Что сделано

### 1. Перенос loader.py из TQA в TQA-MOEX
- **Было:** `~/projects/TQA/services/MOEX_LOADER/loader_ch.py` + крон на TQA
- **Стало:** `~/projects/TQA-MOEX/loader.py` — двойная запись CH + PG
- Логика CH+PG dual write (clickhouse_connect + psycopg2) — из loader_ch.py
- Байпас free-user: `BYPASS_FROM=2020-01-03`, `BYPASS_TILL=2020-01-10d`
- Если PG падает — пишет только в CH, без падения

### 2. Обновлены крон-джобы
| Джоб | Что было | Что стало |
|------|----------|-----------|
| `a2b94581de01` (silent collector, */5) | `cd TQA && python3 services/MOEX_LOADER/loader.py 10` | `cd TQA-MOEX && python3 loader.py` |
| `f11c3ff3280f` (HOURLY report) | prompt с `cd TQA/services/MOEX_LOADER` | prompt с `cd TQA-MOEX` |

### 3. Архив TQA — не удалял файлы
- `~/projects/TQA/services/MOEX_LOADER/loader_ch.py`, `loader.py`, `price_loader.py` — остались в TQA (архивный проект, не трогаем)
- Все рабочие ссылки теперь ведут в TQA-MOEX

### 4. Состояние
- CH `moex.openinterest` — работает через новый путь
- PG `openinterest_moex` — через PG_HOST=10.0.0.63 (primary)
- Венв: `/home/user/venvs/tqa/main/bin/python3` (тот же, что и был)
