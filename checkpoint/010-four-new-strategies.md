# Checkpoint 010 — 4 New Strategies Backtest + VWAP Integration

**Дата:** 2026-06-08
**Проект:** TQA-MOEX

## Что сделано

### 1. Реализованы и протестированы 4 стратегии
Файл: `trading_bot/new_strategies.py` (762 строки)

**Результаты (out-of-sample, последние 30% данных):**

| Стратегия | Параметры | WR | Сигналов | Комментарий |
|:----------|:---------:|:--:|:--------:|:------------|
| **OI Divergence** | lb=20 h=3 | 68.1% | 1-34/тикер | Редкие, но точные сигналы |
| **VWAP Deviation** | dev>2.0 h=12 | 53.2% | >1000/тикер | ✅ Интегрирована |
| **Retail Trap** | fiz_z>1.5 h=12 | 49.3% | >1000/тикер | — |
| **OTC** | oi_z>0.3 h=12 | 45.7% | >1000/тикер | — |

### 2. VWAP интегрирована в trading bot
- `trading_bot/vwap_engine.py` — модуль стратегии
- `trading_bot/__init__.py` — VWAP_TICKERS (GZ, Eu, SR, Si, MC)
- `trading_bot/tracker.py` — VWAP_TICKERS в ALL_TICKERS
- `trading_bot/cron_scanner.py` — блок сканирования VWAP

### 3. Верификация
- Cross-check: независимая реализация VWAP vs интегрированная — **0 расхождений**
- No look-ahead: подтверждено (все индикаторы на data[:i])
- SHORT/LONG returns: правильные
- Healthcheck: ✅
- Полный прогон сканера: ✅ (VS=4, Reversion=29, OB=2, VWAP=3779 сигналов)

## Файлы созданы/изменены

| Файл | Статус |
|:-----|:------:|
| `trading_bot/new_strategies.py` | ✅ новый |
| `trading_bot/vwap_engine.py` | ✅ новый |
| `docs/backtest/otc_results.txt` | ✅ новый |
| `docs/backtest/retail_trap_results.txt` | ✅ новый |
| `docs/backtest/vwap_results.txt` | ✅ новый |
| `docs/backtest/oi_divergence_results.txt` | ✅ новый |
| `docs/backtest/summary.txt` | ✅ новый |
| `trading_bot/__init__.py` | ✅ изменён |
| `trading_bot/tracker.py` | ✅ изменён |
| `trading_bot/cron_scanner.py` | ✅ изменён |
| `docs/plans/2026-06-08-four-new-strategies.md` | ✅ план |

## Что дальше
- Мониторинг VWAP сигналов в рабочее время
- Если Retail Trap даёт >50% WR — интегрировать как 5-ю стратегию
- OI Divergence как фильтр-подтверждение (не самостоятельная стратегия)
