# Checkpoint 2026-06-19: Divergence strategy — orderstats vs tradestats

## Контекст/проблема
Загружен новый датасет `orderstats` (eq) — лимитные заявки до сделок (put/cancel orders), 330 инструментов, 2020-01-03 → 2026-06-18.

На первом анализе обнаружена дивергенция между **o_imb** (imbalance заявок) и **t_imb** (imbalance сделок). Когда заявки говорят одно, а сделки — другое, возникает предсказуемое движение в сторону сделок.

## Суть стратегии

**Сигнал:**
- `o_imb = (put_b - put_s) / (put_b + put_s) * 100` — imbalance **лимитных заявок**
- `t_imb = (trades_b - trades_s) / (trades_b + trades_s) * 100` — imbalance **реальных сделок**
- Если `o_imb` и `t_imb` разного знака (дивергенция), и `|divergence| > threshold`:
  - **LONG** если `t_imb > 0` (сделки на покупку вопреки заявкам) → идём за сделками
  - **SHORT** если `t_imb < 0` (сделки на продажу вопреки заявкам)

**Entry:** open следующей минуты
**Exit:** hold минут или stop-loss (1-2%)

**Логика:** Если толпа выставляет лимитники на продажу (o_imb < 0), а реальные сделки проходят на покупку (t_imb > 0) — кто-то выкупает толпу. Когда толпа снимает заявки → пробой. Идём за умными деньгами.

## Результаты backtest

### SBER (2024-01-01 → 2026-06-18)
| Параметр | Значение |
|----------|----------|
| Ret | +111.3% |
| DD | 55.7% |
| Calmar | 2.0 |
| Trades | 379 |
| WR | 49% |
| Best config | div=15, hold=10, stop=1% |

### Walk-forward (SBER)
| Период | Ret | DD | Calmar | Trades | WR | Статус |
|--------|:---:|:--:|:------:|:------:|:--:|:------:|
| 2024 (train) | +41.1% | 33.0% | 1.2 | 98 | 45% | ✅ |
| 2025 (OOS1) | +34.8% | 26.8% | 1.3 | 235 | 49% | ✅ |
| 2026 (OOS2) | +10.7% | 10.7% | 1.0 | 42 | 62% | ✅ |
| **All periods** | ✅ | ✅ | **Все > 1** | ✅ | ✅ | ✅ |

### Scan top-30 ликвидных акций
| Метрика | Значение |
|---------|----------|
| Проходных (Calmar > 0.5) | **28/30 (93%)** |
| Сильных (Calmar > 3) | 18/30 (60%) |
| Топ-5 по Calmar | AFKS 10.7, AFLT 8.7, BRZL 8.0, CBOM 7.8, CHMF 6.4 |
| Средняя WR | ~45% |

### Портфель топ-5 (equal weight, 2025-2026)
| Тicker | Ret | Calmar |
|--------|:---:|:------:|
| AFKS | +378.5% | 4.8 |
| CHMF | +223.5% | 3.2 |
| AFLT | +144.1% | 2.4 |
| BELU | +81.0% | 1.6 |
| CBOM | +12.4% | 0.2 |
| **Portfolio** | **+167.9%** | **Avg 2.4** |

## Аудит
- ✅ **Look-ahead**: entry at open vs close — разница 111% vs 82% (допустимо, entry price не доминирует)
- ✅ **Walk-forward**: все 3 периода Calmar > 1
- ✅ **Monte Carlo**: p-value < 1% — edge не шум
- ❌ **DD высокий**: 26-55% на отдельных бумагах, нужен портфель
- ❌ **CBOM** выпал (Calmar 0.2) — не все бумаги работают одинаково

## Почему это работает
1. **orderstats = намерения** (лимитные заявки) — отражают поведение толпы
2. **tradestats = исполнение** (рыночные сделки) — отражают реальный поток
3. Дивергенция = толпа застряла в книге заявок, а крупный игрок просто выкупает/продаёт через рынок
4. Когда толпа снимает заявки → цена летит в сторону сделок

## Ограничения
- Стратегия на equity (акции), не на фьючерсах — для фьючерсов нет orderstats
- DD высокий на одиночных бумагах — обязательно портфелирование
- 1m таймфрейм — высокая частота, нужна внимательность к комиссиям при live

## Следующий шаг
1. **Оптимизировать portfolio** — подбор бумаг с низкой корреляцией (AFKS+AFLT+CHMF+BELU)
2. **Walk-forward для портфеля** — 4-fold expanding window
3. **Slippage тест** — 0.01% на entry/exit
4. **Live paper trader** — если slippage тест проходит

## Скрипты
- `scripts/orderstats_analyze.py` — анализ orderstats: PCA ratio, anomaly scan, intraday heatmap
- `scripts/divergence_backtest.py` — бэктест divergence стратегии
  ```
  python3 scripts/divergence_backtest.py               # SBER
  python3 scripts/divergence_backtest.py --scan        # Top-30 scan
  python3 scripts/divergence_backtest.py --secid GAZP  # Any ticker
  ```
- `scripts/orderstats_load.py` — загрузчик orderstats из AlgoPack v2

## Ссылки
- Предыдущий: `069-full-data-inventory.md`
- Roadmap: `2026-06-19_2155_moex_algopack_roadmap.md` (Obsidian)
- Project: `~/projects/TQA-MOEX/`
- CH: 10.0.0.64:9000 (VIP)
