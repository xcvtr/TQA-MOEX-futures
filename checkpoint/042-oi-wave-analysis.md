# Checkpoint 042 — OI Wave Analysis & TRIZ Exit

**Дата:** 2026-06-12

## Что сделано

### 1. Анализ OI волн на всём 2025 годе
- Проверены 12 тикеров на 5m данных (янв-дек 2025)
- Все символы показали **достаточно OI волн для торговли** на полном годе
- Деление на 3 группы:
  - **SHORT only** (yur_net всегда <0): Si, SR, VB, NM, IMOEXF, Eu, CR
  - **LONG+SHORT** (yur_net пересекает ноль): BR, PD, AL, LK, AF

### 2. Скринер символов — `scripts/oi_symbol_screener.py`
- Критерии: количество сигналов, z-crossings, CV yur_net
- Tier 1 (✅ ТОРГОВАТЬ): BR, PD, Si, AF, SR, VB, AL, LK, NM, IMOEXF, Eu, CR
- Tier 2 (⚠️ УСЛОВНО): все 12 имеют достаточно сигналов на 2025 годе

### 3. TRIZ-анализ exit стратегий — `scripts/triz_exit_test.py`
- Протестированы 5 exit-стратегий: current (yz=0.5/h24), h48, TRIZ mean±σ
- **Результат:** ни одна exit-стратегия не даёт профита на 9/12 символах
- Проблема не в exit, а в **entry** — z-score детекция даёт 40-50% WR

### 4. Анализ волн OI напрямую — `scripts/oi_wave_analysis.py`
- **Ключевое открытие:** TROUGH yur_net → LONG даёт 63-75% WR на H1
  - BR 5m: 49 волновых разворотов
  - BR H1: **114 волновых сделок**, WR=58%, итог +59%/год
  - PD H1: 29 сделок, LONG avg +2.1%, SHORT avg -1.15%
- PEAK → SHORT нестабилен (BR 42%)

### 5. Функциональность дашборда 5058
- **Клик по canvas** — ставит start (time-4h) и end (time+4h), чтобы увидеть сделку
- **localStorage persistence** — настройки не сбрасываются по F5
- Исправлен ClickHouse concurrency (thread-safe client)

## Скрипты
- `scripts/oi_exit_analysis.py` — сравнение exit-стратегий
- `scripts/oi_symbol_screener.py` — скрининг символов
- `scripts/oi_wave_analysis.py` — волновой анализ OI
- `scripts/triz_exit_test.py` — TRIZ exit тесты
- `moex_oi_dashboard.py` — дашборд 5058 (с кликами + localStorage)

## Что дальше
- **Переписать entry** — не z-score, а волновые развороты yur_net (TROUGH→LONG, PEAK→SHORT)
- Обновить дашборд с новым entry-движком
- Тест на walk-forward 2025-2026
