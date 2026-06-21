# Checkpoint 2026-06-20: Divergence portfolio — реалистичный тест

## Контекст
Портфельный тест divergence strategy (orderstats vs tradestats) на AFKS, AFLT, CHMF.
Полный реализм: slippage 0.02%, комиссия 0.05%, MTM equity, полный реинвест.
Grid search per ticker.

## Best configs
| Ticker | div_thr | hold | stop |
|--------|---------|------|------|
| AFKS | 10 | 10 | 1% |
| AFLT | 10 | 10 | 1% |
| CHMF | 10 | 10 | 1% |

Все три сошлись на одинаковом конфиге.

## Per-ticker (с расходами)
| Ticker | Ret | DD | Calmar | Trades | WR |
|--------|:---:|:--:|:------:|:------:|:--:|
| AFKS | +504.4% | 17.7% | 28.5x | 511 | 45% |
| AFLT | +380.0% | 12.7% | 29.8x | 500 | 48% |
| CHMF | +292.5% | 12.0% | 24.3x | 471 | 49% |

## Портфель (equal weight, 3 тикера)
| Метрика | Значение |
|---------|----------|
| Capital | 100K → 441,204 RUB |
| Total Return | **+341.2%** |
| Max DD | **5.8%** |
| Calmar | **58.4x** |

### BELU исключена
BELU даёт DD 73% отдельно, в портфеле тянет DD до 46.4%. Исключена.

## Почему DD 5.8% при таком ret
Три низкокоррелированные бумаги с long/short направлениями:
- AFKS — телеком (длинные плечи)
- AFLT — авиаперевозки (циклика)
- CHMF — металлургия (сырьё)
Разные сектора → разные сигналы → одновременный DD крайне редок.
Каждая по отдельности имеет DD 12-18%, но вместе — 5.8%.

## Stress-test (сделан следом)
Скрипт: `scripts/portfolio_divergence_stress.py`

## Ссылки
- Скрипты:
  - `scripts/portfolio_divergence_v4.py` — grid + portfolio, BELU включена
  - `scripts/portfolio_divergence_3tk.py` — 3 тикера без BELU
  - `scripts/portfolio_divergence_stress.py` — stress-test
- Предыдущий чекпойнт: `070-divergence-strategy.md`
- Проект: `~/projects/TQA-MOEX/`
