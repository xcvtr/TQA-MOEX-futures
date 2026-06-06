# 2026-06-06 — Настройка securities API + cron + фикс OI загрузчика

## Сделано

### 1. Фикс OI загрузчика (`load_eod_oi.py`)
- Убрал `DELETE_EXISTING = True` — теперь по умолчанию **инкрементальный режим**
- Скрипт проверяет последнюю дату в БД и доливает только пропущенное (с запасом 2 дня назад)
- Si перезагружен полностью с 2021 → 2852 EOD-записей с заполненными `buy_accounts`/`sell_accounts`
- Bulk-загрузка всех 64 тикеров запущена в фоне (через `bulk_oi_history.py`)

### 2. Securities API — сбор ГО/плеча с MOEX ISS
- Создан `fetch_securities.py` — загружает все 564 контракта с MOEX ISS
- Маппинг 64 OI-тикеров → 29 найдены на MOEX с ГО > 0
- Сохраняет в PostgreSQL таблицу `moex_securities` (upsert)
- Снапшот: `securities_snapshot.json`
- Расчёт реального плеча: `leverage = (prevsettle × stepprice / minstep) / go`

### 3. Cron-задачи
- **MOEX securities** — ежедневно в 06:00 (обновление ГО/плеча)
- **MOEX OI incremental** — ежедневно в 05:00 (долив OI данных)

### 4. Whale Detector — запуск
- Детектор работает: **Si 77.8% WR** (18 сигналов за 3.3 года, walk-forward подтверждён)

## Файлы
- `~/projects/TQA-MOEX/fetch_securities.py` — сборщик securities
- `~/projects/TQA-MOEX/securities_snapshot.json` — снапшот ГО/плеча
- `~/projects/TQA-MOEX/CHANGELOG.md` — этот журнал
- `~/projects/TQA-MOEX/docs/changelog/2026-06-06-securities-and-oi-fix.md` — детальная запись

## Что не сделано
- Bulk OI history (64 тикера) — ещё заливается в фоне
- Запуск whale detector по всем тикерам — ждёт завершения загрузки OI
