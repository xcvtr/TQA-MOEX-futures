# Checkpoint 014 — OB integrated into production: Variant D (Limit at OB Level)

## Что сделано

### 1. Полная замена OB стратегии в production
Старая Order Block (SBERF/BR, `max_lookback_bars=5`, вход на open displacement) удалена. Внедрена **Variant D** — лимитные ордера на OB level.

**Изменённые файлы:**
- `trading_bot/ob_engine.py` — полная перепись: загрузка 5m → ресемпл H1 → displacement → limit fill → сигнал
- `trading_bot/__init__.py` — новый DEFAULT_OB_CONFIG (body_mul=1.5, lookback=20, limit_lookback=5, horizon=2) + 16 тикеров
- `trading_bot/cron_scanner.py` — загрузка 30 дней 5m, фильтр свежести 6ч для OB

### 2. Состав OB в демо

**Core (7 тикеров, полный вес):**
UC, ED, Si, RM, KC, NA, GD — WR 71-74%, DD<5%

**Expansion (9 тикеров, половинный вес):**
RI, LK, SBERF, GK, MC, RN, IMOEXF, YD — WR 70-73%, DD 5-10%

### 3. Всего стратегий в демо: 5
1. Volume Surge — HS, KC, DX, HY, BM
2. Mean Reversion — NM, AF
3. **Order Block (новая)** — 16 тикеров (7+9)
4. VWAP Deviation — GZ, Eu, SR, Si, MC
5. OI Divergence — RI, GL, Si

## Результаты тестов

| Тест | Результат |
|:-----|:----------|
| Загрузка данных (30д 5m) | ✅ UC: 2514 rows |
| Детекция сигналов | ✅ UC: 1 сигнал, ED: 1 сигнал |
| Healthcheck БД | ✅ DB: True |
| git commit | ✅ d6d8b65 |

## Файлы
- `trading_bot/ob_engine.py` — новая OB (Variant D)
- `scripts/ob_redesign_test.py` — тест 3 вариантов
- `scripts/ob_limit_test.py` — тест с лимитными ордерами
- `docs/plans/ob_results/leaderboard_limit.csv` — полные результаты

## Что дальше
- [ ] Наблюдать за сигналами OB в демо 2 недели (Core)
- [ ] При стабильности — добавить Expansion с половинным весом
- [ ] Проверить пересечения: Si есть в VWAP, OI Divergence и теперь OB
