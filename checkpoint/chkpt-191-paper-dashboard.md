# Чекпойнт 191 — Paper Trader + Dashboard v5

**Дата:** 2026-07-26
**Проект:** TQA-MOEX-futures

## Что сделано

### Paper Trader
- Обновлена логика: **per-ticker комиссии** из PG fee_entry, **slippage 2-5 tick**, **volume cap** 20% (Si 50%), **max 20 contracts**, **trend filter** SMA50
- Вынесен из Hermes cron в **системный crontab** (`*/5 15-23,0-4 * * 1-5`)
- Таймаут увеличен 60→120с
- Лог: `/tmp/paper_trader.log`

### Dashboard v5
- `dashboard_v5.py` — порт 8085
- Общий портфель (Capital, MTM, Floating PnL, Closed PnL, DD)
- По стратегиям (карточки с Closed/WR/PF/Open)
- Открытые позиции (Тикер, Стратегия, Dir, Entry, PnL, PnL%)
- История сделок

### Данные
- `scripts/cron_paper_v5.sh` — cron wrapper
- `reports/sweep/` — equity curve, OI анализ, sweep результаты, trades
- `checkpoint/chkpt-190-realistic-slippage.md`
- `report/finam_pgo_rates.md` — ставки КСУР ПГО

### Исправления
- NaN в PnL% дашборда (shares vs contracts)
- Неверный port 8080 (занят старым dashboard.py → порт 8085)
- Paper trader timeout (60→120с)

## Файлы
- `strategies/common/paper_trader.py` — обновлён (fees, slippage, volume cap, trend)
- `run_paper_trader.py` — timeout 120с
- `dashboard_v5.py` — новый дашборд (порт 8085)
- `scripts/cron_paper_v5.sh` — system crontab wrapper
- `AGENTS.md` — обновлён (paper + dashboard)
