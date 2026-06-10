# 025 — V3 Score+ADX Dynamic Time-Stop: +117.6% при DD 23.68%

## Что сделано

### Задача 1 — Time-stop добавлен в portfolio.py
- Параметр `max_hold_bars: int = 40` в сигнатуре `simulate_adaptive_portfolio()`
- Принудительное закрытие позиций через N баров по exit_price (без look-ahead)

### TRIZ + Debate Mode (2 раунда)
- Round 1: 2 subagent-а с противоположными философиями (консерватор vs радикал) предложили 14 идей
- Round 2: радикал раскритиковал консерватора, сформировал TOP-5 гибридов
- Итог: выбрана Score-Каскад + ADX-модулятор

### Задача 2-4 — Полный sweep 72 комбинации
- FIFO vs PF сравнение
- Все 144 симуляции выполнены

### Ключевой прорыв
- **V3 Score+ADX Time-Stop**: `hold_limit = max_hold_bars × (0.5 + score)`, clamp [10, 80]
- ADX модулятор: ADX>25 → ×1.5, ADX<15 → ×0.7
- Итог: **+117.6% return при DD 23.68%, Calmar 4.97**
- Против baseline: +65.2% при DD 45.86% (без time-stop)

## Результаты

| Версия | Return | DD | Calmar | Trades | Win/Loss |
|--------|--------|-----|--------|--------|----------|
| Baseline (без TS) | +65.2% | 45.86% | 1.42 | 519 | 2.05× |
| ❌ TS 40 bars (exit_price) | +4.3% | 20.40% | 0.21 | 301 | 1.22× |
| **V3 Score+ADX** | **+117.6%** | **23.68%** | **4.97** | 350 | **2.30×** |
| FIFO best ≤10% DD | +39.5% | 7.78% | 5.08 | 891 | — |

## Исправленные баги OpenCode
1. `pnl = 0.0` в time-stop — обнулял всю прибыль
2. `close_price = sig['entry']` для чужих тикеров — PnL по неверной цене
3. Rollover внутри `if max_hold_bars > 0:` — при max_hold_bars=0 отключался rollover
4. Score-Immunity (OpenCode v3) — immune-позиции блокировали капитал навсегда

## Файлы
- `trading_bot/portfolio.py` — V3 Score+ADX динамический time-stop
- `scripts/portfolio_sweep.py` — sweep с кэш-загрузкой
- `.signals_cache.json` — 7093 сигнала

## Что дальше
- [ ] ERE (Expected Remaining Edge) — следующий уровень: закрывать по матожиданию, не по времени
- [ ] Trailing stop (для текущего тикера при новом сигнале)
- [ ] Оптимизация параметров score_immunity_threshold, max_hold_bars
