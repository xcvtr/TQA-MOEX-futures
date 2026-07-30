# Checkpoint 195: Финальный портфель — 3 стратегии, +1,228% ROI, MDD 14.3%

**Дата:** 2026-07-30
**Теги:** checkpoint, portfolio, final, verified

## Результаты (FINAM mt5_continuous, 365 дней, common pool)

**Капитал:** 200,000 → **2,723,954** (+1,262%)  
**MTM MDD:** 14.30% ✅

## Состав портфеля (3 стратегии)

| Стратегия | Тикер | Detect | Risk | Сделок | WR | PF | PnL |
|:----------|:------|:-----:|:----:|:------:|:--:|:--:|----:|
| ⚡ IR | Si | M1 | 10% | 502 | 49.6% | 2.39 | +1,597K |
| 🐉 Dragon | GD | 10m | 20% | 145 | 56.6% | 3.78 | +534K |
| 🐉 Dragon | NG | 3m | 20% | 857 | 47.0% | 1.87 | +364K |

## Отклонённые кандидаты
- **SH RN** — PF=0.50 (убыточна), независимая проверка не подтвердила
- **Dragon MM** — низкая ликвидность (20 контрактов/бар), +24K
- **Dragon SV** — +81K при risk=40%, малый вклад
- **Dragon GZ** — PF=0.83 в портфельном контексте (убыточно)
- **Dragon BR** — PF<1, убыточно

## Аудит
- Look-ahead: нет (lo_hist/close_hist без текущего бара ✅)
- Комиссия: per-ticker fee_entry, round-trip ✅
- Slippage: 2-5 tick entry, 1 tick exit ✅
- Trend filter: SMA50 на detect барах ✅
- Volume cap: 0.2 (20% объёма), 0.5 для Si ✅

## Paper Trader
- Портфель в PG: Si IR + GD Dragon + NG Dragon
- Оба трейдера сброшены, Equity 200K
- Cron: `*/5 15-23,0-4 * * 1-5`

## Update: live-директория + deploy

### Added
- `strategies/common/live/` — изолированная копия paper_trader, broker, executor, engine
- `scripts/deploy_live.sh` — скрипт деплоя (копирует из разработки в live)
- Скилл `deploy-moex-paper` — процедура деплоя

### Fixed
- `entry_time` TypeError — `p['entry_time']` хранится как строка, не datetime. Добавлен `datetime.fromisoformat()`

### Состояние paper trader
- v5: Equity 200,000, 0 позиций, ждёт открытия рынка
- v6: Equity 200,000, 0 позиций
- Портфель PG: Si IR + GD Dragon + NG Dragon
- Cron: `*/5 15-23,0-4 * * 1-5`
