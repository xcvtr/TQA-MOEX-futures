# Stress Test: всё системно

> **Цель:** Найти конфигурацию портфельной стратегии на MOEX (6 тикеров: GL, HS, HY, RN, NM, AF), которая даёт положительную доходность **с комиссиями 4₽/сделку (round-trip)**, проскальзыванием и без look-ahead.

## Параметры для перебора

### Таймфреймы
- M5 (оригинальные бары)
- M15 (resample)
- H1 (через resample 60min)
- H4 (через resample 240min)
- D1 (один бар в день)

### Параметры симуляции
- lot: [0.10, 0.15, 0.20, 0.25, 0.50]
- bars_left: [4, 8, 13, 21, 34]
- stop_atr: [1.0, 1.5, 2.0, 3.0]
- score_thresh: [0.10, 0.15, 0.20, 0.25, 0.30]

### Score компоненты (перебор весов)
- vs (volume surge): вес 0–0.5
- os_ (OI spread): вес 0–0.5
- oi_accel: вес 0–0.5
- fiz_yur_delta: вес 0–0.5
- vz (volume z-score): вес 0–0.3

### Фильтры
- ADX > 20 (только трендовые дни)
- Только утро (10-14) или день (15-20)
- Минимальный ATR (atr_pct > 0.1%)
- Только LONG / только SHORT / LONG+SHORT

## Критерий отбора
1. CAGR > 0% (обязательно)
2. DD < 20% (обязательно)
3. Calmar > 1.0 (желательно)
4. Минимум 50 сделок (статистическая значимость)

## Процедура

1. Взять `scripts/stress_test.py` — уже есть функция `run_sim(data, lot, bars, stop, score_thresh)`
2. Расширить её: slippage, SHORT, score weights, фильтры
3. Прогнать grid: все TF × параметры
4. Отсортировать по Calmar, показать топ-20
5. Для топ-5 конфигов — прогнать per-symbol breakdown

## Данные

- ClickHouse: moex.prices_5m + moex.prices_5m_oi
- Период: 2023-2026 (прогрев), 2025-01 — 2026-04 (OOS)
- Тикеры: GL, HS, HY, RN, NM, AF
- GO: TICKER_CONFIGS из scripts/bar_level_sim.py
- Комиссия: 4₽/сделку (round-trip, 2₽ × 2)
- Проскальзывание: 0.1% от entry (опционально)

## Скрипты

- `scripts/stress_test.py` — текущая версия с unified simulate
- `scripts/portfolio_e6_unified.py` — предыдущая (без комиссий)
- `scripts/hyperdrive.py` — 7 экспериментов (без комиссий)
- `config.py` — CH_HOST, CH_PORT, CH_DB
