# 🔥 Checkpoint 155 — Paper Trader OHLC Fix + AlgoPack Recovery

**Дата:** 2026-07-07
**Проект:** TQA-MOEX-futures

---

## OHLC баги в paper_trader.py

ISS `moex.prices_5min` каждые 5 мин отдаёт снэпшот с дневными hi/lo. Использование `max(hi)` и `min(lo)` давало hi_range=0, lo_range=0 для всех баров → **сигналы Stop Hunt никогда не срабатывали**.

**Фикс:** агрегировать OHLC из `prc` (цена закрытия снэпшота) в 5-мин окне:

```python
opn = argMin(prc, bt)  # первая цена в окне
hi  = max(prc)          # максимальная цена среди снэпшотов
lo  = min(prc)          # минимальная цена
prc = argMax(prc, bt)  # последняя цена
```

Теперь hi_range=453 для Si (было 0), lo_range=453. Сигналы корректные.

## AlgoPack Recovery

`tradestats_fo` застрял 19 июня. Причины:
- **Нет крона** — `moex-algopack-daily` на паузе, `run_daily.sh` не запускался
- **413 Request Entity Too Large** — nginx режет большие INSERT'ы через порт 8123
- **tradetime type mismatch** — API возвращает datetime, CH ждёт String
- **cd path** — `run_daily.sh` в неверную директорию

**Фиксы:**
- CH порт 8124 (прямой доступ, без nginx)
- `insert_batch`: чанки по 1000 строк, retry по 200
- `convert_row`: tradetime → строку, SYSTIME → строку
- Крон: `30 6 * * 1-5` инкрементальная загрузка tradestats
- `run_daily.sh`: cd fix

## Структура данных (обновлённая)

| Таблица | Назначение | Живые данные? |
|---------|-----------|:------------:|
| `tradestats_fo` | AlgoPack OHLCV для бэктестера | EOD (до вчера) |
| `prices_5min` | ISS snapshot цен для paper trader | **live** (каждые 5 мин) |
| `bars` | AlgoPack 5-min bars | до 1 июля (не обновляется) |
| `backtest.*` | Результаты прогона тестера | статика |

## Paper Trader — полный цикл готов

```
Cron:  */5 15-23 * * 1-5 → run_paper_trader.sh → .venv/bin/python3
                                                 → paper_trader.py
                                                     → get_latest_bars(prices_5min, OHLC agg)
                                                     → check_signal() — Stop Hunt
                                                     → manage_positions — trailing TP, SL
                                                     → save_state → PG futures.paper_state + paper_trades

Дашборд: http://10.0.0.60:8087/  (scripts/dashboard.py)
```

**Конфиги:** PG `futures.portfolio` (contracts=1), `futures.ticker_specs` (КНУР ×2.8 GO)

## Файлы изменений

- `strategies/common/paper_trader.py` — OHLC из prc, CH_HOST .60
- `scripts/run_paper_trader.sh` — venv fix
- `scripts/dashboard.py` — новый
- `moex-algopack-loader/scripts/load_algopack_fo.py` — port 8124, chunks
- `moex-algopack-loader/run_daily.sh` — cd fix
- `moex-algopack-loader/` — AGENTS.md, README.md (данные)
