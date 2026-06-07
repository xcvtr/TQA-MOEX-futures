# 008 — Order Blocks (ICT Smart Money) — третья стратегия в бота

**Дата:** 2026-06-08
**Статус:** OB добавлен в cron_scanner как третья стратегия

---

## Что сделано

### 1. `trading_bot/ob_engine.py` — новый модуль
- `detect_order_block_signals(symbol, rows, config)` — детекция Order Blocks
- `load_price_data(symbol, days)` — загрузка 5m данных
- Логика: displacement (body > 1.5× медиана, range > 1.2× медиана) → OB = свеча перед ним → вход на открытии displacement

### 2. `trading_bot/__init__.py` — конфиг
- `DEFAULT_OB_CONFIG` — настройки по умолчанию (body_mul=1.5, horizon=4, max_lookback_bars=5)
- `OB_TICKERS` — тикеры: SBERF, BR, NM, AF (те же, что и Reversion, но с отдельным конфигом)

### 3. `trading_bot/cron_scanner.py` — сканирование
- Сигналы OB собираются в отдельном цикле перед мержем
- Статус-лайн: `VS: X sig | Reversion: Y sig | OB: Z sig | Open: N`
- lookup цепочек: `TICKERS → REVERSION_TICKERS → OB_TICKERS`

### 4. `trading_bot/dashboard.py` — визуализация
- Новая карточка "🟣 Order Block (ICT)" с WR/PF
- Сегрегация сделок по стратегии в equity-кривой

---

## Порядок сканирования в кроне
```
1. Volume Surge (SCAN_SYMBOLS → engine)
2. Mean Reversion (REVERSION_TICKERS → reversion_engine)
3. Order Blocks (OB_TICKERS → ob_engine)
4. ADX фильтр на все
5. Только сигналы за последние 30 мин
6. Check exits → Open new positions
```

---

## Результаты backtest (из checkpoint 007)
| Тикер | Напр | n | WR% | PF | DD% |
|:-----|:----:|:-:|:---:|:--:|:---:|
| SBERF | LONG h=4 | 4,697 | 69.9% | 4.27 | 2.0% |
| SBERF | SHORT h=4 | 4,816 | 70.8% | 3.60 | 2.6% |
| BR | LONG h=4 | 5,201 | 71.7% | 2.06 | 192% |
| NM | LONG h=4 | 4,096 | 67.1% | 2.16 | 30.2% |
| AF | LONG h=4 | 4,390 | 67.4% | 2.17 | 28.4% |

---

## Замечание
SBERF оказывается и в REVERSION_TICKERS, и в OB_TICKERS. На практике это не проблема — бот не откроет две позиции на один тикер (active_symbols check). Но в перспективе можно разделить: OB на BR/AF, Reversion на NM/SBERF.
