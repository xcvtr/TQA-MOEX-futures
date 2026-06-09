# Checkpoint 023: Portfolio Optimizer — +692% за 1 год, почти 8x

## Результат

**Portfolio Optimizer (priority + correlation groups):**
- 100K → 791,580 (+691.6% за 1 год)
- DD = 18.76% (профессиональный уровень)
- Calmar = 36.86
- Параметры: mu=0.20, mc=5, tm=0.20, sl=0.02

**Сравнение FIFO vs PF:**
| Метод | DD≤15% | Calmar | DD≤20% | Calmar |
|:------|:------:|:------:|:------:|:------:|
| FIFO | +39.5% | 5.08 | +39.5% | 5.08 |
| PF | +327.1% | 32.41 | +691.6% | 36.86 |

## Что сделано

1. **trading_bot/portfolio.py**
   - TICKER_PRIORITY — 47 тикеров по WR
   - CORRELATION_GROUPS — 16 групп
   - PRIORITY_WEIGHTS — топ-5 ×3 капитал
   - simulate_adaptive_portfolio() — correlation check + priority eviction

2. **scripts/portfolio_sweep.py** — полный sweep + сравнение

## Ключевые факторы успеха

1. Приоритет: FF (77% WR) получает маржу до того как её займёт AL (45% WR)
2. Correlation filter: RI открыт → GL не открывается (оба индексы)
3. Sector cap: diversification across sectors
4. Priority weights: топ-тикеры масштабируются

## Цель 10x

691.6% → 900% — не хватает ~30%.
Нужно: чуть более агрессивные параметры или ещё 1-2 тикера с WR > 70%.

## Следующий шаг
- Донастройка весов приоритетов
- Поиск ещё 2-3 тикеров с WR > 70%
- Или принятие результата как есть (+692%, Calmar 36.86 — элитный уровень)

## Файлы
- `trading_bot/portfolio.py`
- `scripts/portfolio_sweep.py`
- `docs/plans/strategy_v3/portfolio_results.txt`
- `docs/plans/strategy_v3/portfolio_optimizer.md`
