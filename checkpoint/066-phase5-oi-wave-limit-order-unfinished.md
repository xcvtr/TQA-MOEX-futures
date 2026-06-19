# Checkpoint 2026-06-12: Phase 5 — OI-wave analysis завершён, limit order simulation с багом

## Контекст

После чекпойнта 065 (11 июн) на Phase 5 оставались недоделанные пункты:
- ✅ Per-ticker threshold grid
- ❌ TF sweep для OI-wave
- ❌ Limit order simulation (fallback-to-market)
- ❌ Bar-level OI-wave walkforward
- ❌ Итоговая сборка Phase 5

## Что сделано

### 1. Per-ticker threshold grid
Скрипт `phase5_portfolio_oi_correlation.py` — grid search по threshold 0.15–0.70 для каждого тикера.
- **Результат:** лучшие тикеры имеют threshold 0.15–0.35 (low threshold = чувствительная детекция)
- **Проблема:** grid per-ticker не дал статистически значимого улучшения против единого threshold
- **Вывод:** единый threshold 0.25/0.20 оптимален для OI-wave

### 2. TF sweep для OI-wave
Сделано в `phase5_tsweep_additional.py`:
- 5m → 5,804 сделок, DD -13.2%, профит не указан
- 15m → 434 сделки, -14.7% DD
- H1 → 156 сделок, DD не указан
- **Результат:** OI-wave оптимален на 5m таймфрейме. Дольше — сделок слишком мало

### 3. OI-wave analysis на 63 тикерах (3 итерации)
Скрипт `phase5_oi_wave_fullscan.py`:
- **Итерация 1 (01-05):** Score-filtered, hold_bars=5, 1% на сделку → плохо
- **Итерация 2 (06-10):** Обратный OI (1=fall, -1=rise) → ещё хуже
- **Итерация 3 (11-13):** H1 таймфрейм → 43 сделки на весь период, неинтересно
- **Итерация 4 (14-18):** M30 timeframe, long+short отдельно, 0.5% на сделку → -31.96%, не работает
- **Итерация 5 (19-24):** TP/SL добавлены (TP=3x, SL=1.5x ATR), 1% на сделку → WR 96.3%, PF 3.37, +200.08% за 19 сделок. Но 19 сделок за 1.5 года — слишком мало. **Отброшено**

**Вывод по 4 итерациям OI-wave на 63 тикерах: OI-wave как самостоятельный сигнал не работает.** Слишком мало сделок, низкая предсказательная способность. OI нужно использовать как фильтр, а не как основной entry signal.

### 4. Limit order simulation (НЕДОДЕЛАНО — баг)
Скрипт `limit_order_sim.py` — копия `5m_slippage_sweep.py` с тремя режимами:
- **market** — market entry со slippage
- **limit+fallback** — try limit at best_bid/ask, fallback to market через 1 бар
- **limit+fallback_skip** — try limit, skip if not filled

**Обнаруженный баг:** market mode даёт только 719 сделок на полном периоде (65K баров) против 24K+ у оригинального `5m_slippage_sweep.py`. Причина не найдена — precompute_signals работает корректно (168K raw entry-кандидатов). Проблема внутри simulate(). Подозрение: `avail` быстро уходит в 0 из-за того что ГО не возвращается при закрытии позиций, либо баг в расчёте `locked`.

## Известные баги и недоделки

- ⚠️ `limit_order_sim.py` — баг simulate(), market mode даёт 719 сделок вместо ожидаемых 40K+
- ⚠️ OI-wave как самостоятельная стратегия не работает — требует переработки как фильтр
- ⚠️ Phase 5 всё ещё не собрана в финальный pipeline

## Активные дашборды/сервисы

- TQA-FOREX DOM dashboard: `http://10.0.0.60:5052` (на хосте)
- MOEX OI dashboard: см. чекпойнт 036-moex-oi-dashboard

## Данные

- Forex DOM: PostgreSQL `10.0.0.63:5432` + ClickHouse `127.0.0.1:8123`
- Скрипты в `~/projects/TQA-MOEX/`: `phase5_portfolio_oi_correlation.py`, `phase5_oi_wave_fullscan.py`, `limit_order_sim.py`, `5m_slippage_sweep.py`

## Что дальше

1. **Отладить баг simulate() в `limit_order_sim.py`** — проверить расчёт `cash`, `locked`, цикл entry. Сравнить с `5m_slippage_sweep.py` построчно
2. **Доделать limit order simulation** — получить результаты для всех трёх режимов
3. **OI-wave bar-level walkforward** — переписать OI-wave как фильтр для Volume Surge, а не самостоятельный сигнал
4. **Собрать Phase 5** — финальный pipeline с лучшими компонентами

## Ссылки

- Предыдущий чекпойнт: `065-final-phase5-oi-wave-analysis.md`
- Репозиторий: `~/projects/TQA-MOEX/`
