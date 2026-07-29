# Checkpoint 192: FINAM Docker + DOM + MT5 snapshot + Paper v6

**Дата:** 2026-07-29
**Теги:** checkpoint, infrastructure, finam-docker, dom, mt5-snapshot, paper-v6, timescaledb

## Что изменилось

### 1. FINAM MT5 в Docker
- Заменён хостовой FINAM MT5 (через `wine-runner.sh`) на Docker контейнер `mt5-finam`
- Контейнер на базе `mt5:latest`, bind mount FINAM wine prefix + bridge скрипты
- VNC порт 5901, display :5, пароль `finam`
- **Больше нет плодовитости процессов** — 1 terminal64 + 1 wineserver вместо 28

### 2. Сбор стакана (DOM) через Python API
- Встроен прямо в `mt5_moex_bridge.py` — функции `_dom_send()` и `_mt5_snapshot()`
- **API сервер** `dom_api.py` (:8808) — принимает POST JSON, пишет в PG
- `market_book_add()` + `market_book_get()` работают через Python MetaTrader5 API
- **MQL5 советник больше не нужен** — сбор идёт из bridge
- Автозапуск: `dom_api.py` стартует из `starter.sh`

### 3. Данные в PostgreSQL (TimescaleDB)

| Таблица | Назначение | Hypertable | Compression |
|:--------|:-----------|:-----------|:------------|
| `public.dom` | Стакан 10 уровней bid/ask, 11 тикеров | ❌ (в схеме futures была, пересоздана) | — |
| `public.mt5_account` | Баланс, equity, margin, GO | ✅ 1 день | 7 дней |
| `public.mt5_positions` | Открытые позиции из MT5 | ✅ 1 день | 7 дней |
| `public.mt5_deals` | История сделок (24ч) | ✅ 1 день | 7 дней |

Установлен TimescaleDB в БД `moex` на `10.0.0.60`.

### 4. BrokerDOM + Paper Trader v6
- `strategies/common/broker_dom.py` — BrokerDOM, исполнение по реальному стакану
- `strategies/common/paper_trader_v6.py` — копия paper_trader с DOM-исполнением
- `strategies/common/orderbook_executor.py` (внешний, в finam-bridge)
- `run_paper_trader.py` — новый флаг `--broker dom`
- state-key: `portfolio_v6`

### 5. Cron почищен
- `wine-runner.sh` удалён
- Все wine-запуски заменены на `docker exec mt5` (AlfaForex) и `docker exec mt5-finam` (FINAM)
- `@reboot run_finam_terminal.sh` убран

## Архитектура

```
Хост (10.0.0.60)
├── VNC 5900 — AlfaForex MT5 (контейнер mt5)
├── VNC 5901 — FINAM MT5 (контейнер mt5-finam)
│
└── Контейнер mt5-finam
    ├── Xvfb :5 + x11vnc 5901 + XFCE
    ├── dom_api.py :8808 (API → PG)
    ├── terminal64.exe (окно в VNC)
    └── mt5_moex_bridge.py --loop
        ├── M1 бары → CH moex.mt5_continuous
        ├── Стакан (DOM) → public.dom (PG)
        └── Снимки счёта → public.mt5_account / positions / deals (PG)
```

## Новые файлы

### В проекте TQA-MOEX-futures
- `strategies/common/broker_dom.py` — BrokerDOM
- `strategies/common/paper_trader_v6.py` — Paper Trader v6 (DOM)
- `scripts/cron_paper_v6.sh` — cron wrapper
- `run_paper_trader.py` — обновлён (флаг `--broker dom`)

### Вне проекта (finam-bridge)
- `/home/user/.hermes/finam-bridge/entrypoint.sh`
- `/home/user/.hermes/finam-bridge/starter.sh`
- `/home/user/.hermes/finam-bridge/engine/dom_api.py`
- `/home/user/.hermes/finam-bridge/engine/dom_reader.py`
- `/home/user/.hermes/finam-bridge/engine/orderbook_executor.py`
- `/home/user/.hermes/finam-bridge/engine/dom_collector.mq5` (MQL5 — не используется)
- `/home/user/.hermes/finam-bridge/engine/dom_collector_script.mq5` (MQL5 — legacy)

## PG таблицы

```sql
-- Схемы
public.dom                  -- стакан (ticker, ts, side, price, volume)
public.mt5_account          -- account_info (balance, equity, margin, go_total)
public.mt5_positions        -- positions (ticker, direction, volume, profit)
public.mt5_deals            -- history deals (deal_id, ticker, profit, commission)
```

## Состояние для продолжения

1. Прикрепить **DOM Collector** к графику если нужен MQL5 путь (не обязательно — Python API работает)
2. Запустить paper v6: `python3 run_paper_trader.py --state-key portfolio_v6 --broker dom`
3. Дашборд: `dashboard_v5.py :8085` (не исправлен, висит на `portfolio_v5`)
4. При пересоздании контейнера: `docker run -d --name mt5-finam --restart always --network host --shm-size=512m -e VNC_DISPLAY=5 -e VNC_PORT=5901 -e VNC_PASS=finam -e WINEPREFIX=/root/.mt5 -v /home/user/.wine-finam:/root/.mt5 -v /home/user/.hermes/finam-bridge:/app mt5:latest bash /app/entrypoint.sh`

## Update: post-checkpoint fixes

### Bug fixes
- **Snapshot не писался** — удалён `__pycache__`, исправлена ошибка типа в except. Работает: `mt5_account` пишет balance/equity/margin каждую минуту.
- **Плодовитость terminal64 в AlfaForex** — добавлен `&& wineserver -k` в cron для `docker exec mt5` (bars-updater и mt5-bridge). Каждый запуск теперь убивает wineserver после завершения.

### Dockerfile
- `docker/Dockerfile.finam` — версионированный образ `mt5-finam:1.0.0` (FROM mt5:latest + bridge скрипты)
- Контейнер пересоздан из образа, mount только `/root/.mt5` (wine prefix)

### Cron
- v5: `*/5 15-23,0-4 * * 1-5 scripts/cron_paper_v5.sh`
- v6: `*/5 15-23,0-4 * * 1-5 scripts/cron_paper_v6.sh`
- Оба сброшены (clean state, 200K капитала, 0 позиций)

### Состояние на 29 июля 12:00 IRKT
- Бары: свежие (11/11, MSK)
- Стакан: 11 тикеров, 40 уровней, раз в минуту
- Snapshot: account_info раз в минуту
- terminal64: 2 (AlfaForex + FINAM)
- MT5 в VNC: 5901 (пароль finam)

## Update 2: bugfixes

### Fixed: STATE_KEY не работал
- `global STATE_KEY` отсутствовал в `main()` paper_trader.py и paper_trader_v6.py
- Все state-key игнорировались — v5 и v6 писали в общую таблицу `futures.paper_state`
- Исправлено: `set STATE_KEY` через `__main__.STATE_KEY` / `pt.STATE_KEY`
- Удалён старый state из `futures.paper_state`
- Оба трейдера теперь пишут в свои таблицы (`portfolio_v5`, `portfolio_v6`)

### Fixed: хостовые terminal64 плодились systemd сервисами
- `mt5-terminal.service` — хостовой MT5 терминал (Wine + Xvfb)
- `tqa-moex-mt5-bridge.service` — хостовой FINAM bridge (auto-restart плодил процессы)
- `tqa-fx-terminal.service` — хостовой FX терминал
- `~/.config/autostart/mt5.desktop` — автозапуск при старте XFCE
- Все отключены: `systemctl --user disable`, autostart удалён

### Fixed: cron v6 не запускался
- `set -euo pipefail` в cron_wrapper v6 убивал скрипт при первой ошибке
- `python3` без полного пути в cron (PATH не включал venv)
- Исправлено: убран `set -e`, добавлен PATH с Hermes venv

### Fixed: стакан (DOM) не собирался
- `market_book_get()` возвращал None после `copy_rates_from_pos()` из-за занятого соединения
- Исправлено: новый `mt5.initialize()` + 2s sleep + `market_book_add()` для каждого символа
- Стакан: 64 строки × 11 тикеров, раз в ~85 секунд

### Состояние на 29 июля 17:30 IRKT
- terminal64: 2 (AlfaForex + FINAM) — только Docker
- Хостовые user terminal64: 0
- v5: 1 позиция (Si SHORT impulse_return, entry 88404)
- v6: 1 позиция (Si SHORT impulse_return, entry 88404, slippage 2 tick)
- Оба на cron `*/5 15-23,0-4 * * 1-5`
- Бары: 11/11, свежие (18:19+ MSK)
- Стакан: 11 тикеров, 64 rows, раз в минуту
- Snapshot: account_info каждую минуту
