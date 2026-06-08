# Checkpoint 013 — OB Redesign: 3 variants test + Limit Order Retest

## Что сделано

### 1. Аудит текущей OB стратегии
- Order Block в production (SBERF, BR) имела `max_lookback_bars=5` — искала displacement **только в последних 5 барах 5m**
- `full_scan_v3.py` имел битые импорты — никогда не выполнялся
- Текущая реализация входила на open displacement (breakout), а не на OB level retest
- WR 62-72% из прошлого бэктеста — невоспроизводимы

### 2. Тест 3 вариантов OB на 63 тикерах × 4 ТФ (sk: ob_redesign_test.py)
**Данные:** moex_prices_5m (Окт 2024 — Май 2026), ресемпл до H1/30m/15m/5m

| Variant | Avg WR | Avg PF | Сигналов | Суть |
|:--------|:------:|:------:|:--------:|:-----|
| **A** (Displacement Breakout) | **72.9%** | **5.03** | 9.7M | Вход на open impulse |
| B (ICT Retest) | 51.6% | 1.43 | 8.1M | Ждёт возврата к OB level |
| C (OB Level Entry) | 63.2% | 2.51 | 5.3M | Вход на low/high OB |

**Вывод: Variant A доминирует на всех ТФ. B — мёртв (≈монетка).**

### 3. Лимитный ордер retest (sk: ob_limit_test.py)
Замена рыночного входа на лимитный ордер по OB уровню:

| Variant | Avg WR | Avg PF | Fill Rate | Суть |
|:--------|:------:|:------:|:---------:|:-----|
| **D** (Limit на OB level) | **67.4%** | **2.86** | **51.5%** | ✅ Рабочий |
| E (Limit на close) | 43.9% | 0.70 | 100% | ❌ Проигрышный |

**Variant D — рекомендован для production.** ~51% displacement-сигналов добивают до OB уровня, 67-74% из них прибыльны.

## Результаты

### Core Portfolio (7 тикеров, DD <5%)
UC (73.5% WR, 2.53% DD), ED (73.2%, 2.57%), Si (73.8%, 3.32%), RM (74.1%, 4.01%), KC (71.4%, 4.79%), NA (70.8%, 4.80%), GD (71.7%, 4.83%)

### Expansion Tier (9 тикеров, DD 5-10%)
RI, LK, SBERF, GK, MC/MX, RN, IMOEXF, YD — с половинным весом после верификации core

## Рекомендации квант-трейдера
1. **Threshold DD = 5%** на стратегию — при avg return 0.1-0.5% сделка просадка >10% ломает risk/reward
2. **Core first** — 7 🟢 тикеров в paper на 2 недели
3. **Expansion** — 9 🟡 тикеров +2 нед с половинным весом
4. **Live** — только если portfolio DD < 3% через месяц бумаги
5. **Исключить** VB (430% DD), PT (26% DD), все 🟠 (DD 10-20%) — слишком высокий риск для микроприбыли

## Файлы
- `scripts/ob_redesign_test.py` — тест 3 вариантов OB
- `scripts/ob_limit_test.py` — тест с лимитными ордерами
- `docs/plans/2026-06-06-ob-redesign-test.md` — план теста
- `docs/plans/2026-06-06-ob-retest-limit.md` — план лимитного теста
- `docs/plans/ob_results/leaderboard.csv` — полные результаты 2583 комбо
- `docs/plans/ob_results/leaderboard_limit.csv` — результаты лимитного теста

## Что дальше
- [ ] Интегрировать Variant D в `ob_engine.py` для production
- [ ] Настроить демо-торговлю на 7 core тикеров (H1, h=2)
- [ ] Запустить paper на 2 недели
- [ ] Подключить alerting через Telegram/Matrix
