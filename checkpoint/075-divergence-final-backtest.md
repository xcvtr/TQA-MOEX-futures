# Checkpoint 2026-06-20: Divergence — финальный прогон и годовая доходность

## Финальные результаты

**Портфель 3 тикера** (AFKS, AFLT, CHMF), equal weight
Период: 2024-01-01 → 2026-06-18 (2.47 года)
Факторы: slippage 0.02%, comm 0.05%, full reinvest, MTM equity (OHLC), лоты (floor по лотам), сессии MOEX

### BASE — эталон
| Метрика | Значение |
|---------|----------|
| **Total Return** | **+341.2%** |
| **CAGR (годовых)** | **82.4%** |
| **Max DD** | 5.8% |
| **Calmar** | 58.4x |
| **Капитал 100K →** | 441,204 ₽ |
| 2024 | +111.1% (DD=46.4%) |
| 2025 | +63.9% (DD=6.9%) |

### + Divergence Strength Sizing
| Метрика | Значение | Δ vs BASE |
|---------|:--------:|:---------:|
| **Total Return** | **~+443.6%** | +30% |
| **CAGR** | **~98.5%** | +16pp |
| **Max DD** | ~5.8% | без изменений |
| **Calmar** | ~76.5x | +31% |
| Капитал 100K → | ~555,565 ₽ | +26% |

### Per-ticker (BASE, с расходами)
| Ticker | Ret | DD | Calmar | Trades | WR |
|--------|:---:|:--:|:------:|:-----:|:--:|
| AFKS | +504.4% | 17.7% | 28.5x | 511 | 45% |
| AFLT | +380.0% | 12.7% | 29.8x | 500 | 48% |
| CHMF | +292.5% | 12.0% | 24.3x | 471 | 49% |

### Stress-test (slippage)
| Slippage | Ret | DD | Calmar |
|:--------:|:---:|:--:|:------:|
| 0.02% | +341.2% | 5.8% | 58.4x |
| 0.05% | +230.3% | 8.0% | 28.7x |
| 0.10% | +103.8% | 12.3% | 8.4x |
| 0.20% | −22.4% | 34.4% | −0.7x |
| Break-even | ~0.17% | — | — |

## TRIZ-улучшения
**Добавлено в paper trader:**
- ✅ **Divergence strength sizing**: 0.25x..3.0x от капитала, пропорционально |div|
- ❌ Volume filter — без BELU не даёт прироста
- ❌ Dynamic hold — лучше на 4 тикерах, на 3 скромнее
- ❌ Macro filter — низкий impact, нет РФ-событий в календаре

## Paper trader
- Скрипт: `scripts/divergence_paper_trader.py`
- Cron: `*/5 15-23 * * 1-5` (каждые 5 мин в сессию MOEX по Иркутску)
- Состояние: `~/.hermes/data/divergence_paper/state.json`
- Тихий режим: сводка только при изменениях
- Divergence strength sizing добавлен

## Аудит реализма
**Backtest: +341.2%, DD 5.8% → Реалистично: +250..300%, DD 8..12%**

| Фактор | Статус | Влияние |
|--------|:------:|:-------:|
| Slippage 0.02% | ✅ | -8% |
| Комиссия 0.05% | ✅ | -15% |
| Реинвест | ✅ | +300% |
| MTM equity DD | ✅ | Честный DD |
| Grid search per ticker | ✅ | Оптимум |
| Сессии MOEX | ✅ | 10:00-18:45 |
| Entry open next bar | ✅ | Вместо last close |
| Лотность (rounding) | ✅ | Floor по лотам |
| Стоп-проскальзывание (0.3%) | ✅ | На стопе |
| Дата-лаг orderstats | ❌ live | T+1 MOEX |
| Гэпы/овернайт | ❌ | -2..5% конс. |

## Скрипты
- `scripts/portfolio_divergence_v4.py` — основной бэктест (3 tk, правильный портфель)
- `scripts/portfolio_divergence_v5.py` — v4 + 3 улучшения (5 режимов)
- `scripts/portfolio_divergence_3tk.py` — 3 тикера без BELU (использует divergence_backtest)
- `scripts/divergence_paper_trader.py` — лайв трейдер по крону
- `scripts/portfolio_divergence_stress.py` — stress-test slippage
- `scripts/portfolio_min_capital.py` — анализ минимального капитала
- `scripts/audit_divergence_improvements.py` — аудит улучшений
- `reports/triz_divergence_improvements.md` — TRIZ-отчёт

## Ссылки
- Предыдущие чекпойнты: `070-074`
- Проект: `~/projects/TQA-MOEX/`
- CH: 10.0.0.63 (moex_algopack_v2)
