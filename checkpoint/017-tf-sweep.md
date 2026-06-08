# Checkpoint 017 — Per-ticker TF sweep: VWAP, Reversion, OI Divergence

## Что сделано

### Per-ticker TF optimization для всех стратегий
Протестированы 5m, 15m, 30m, H1 для VWAP, Mean Reversion, OI Divergence (2 года данных).

### Результаты sweep

**VWAP Deviation — 5m однозначно лучший ТФ** для GZ, SR, MC.
- Si: 15m даёт +0.9% (53.6% vs 53.3%) — marginal, оставлен 5m
- На 15m/30m/H1 WR падает на 2-8% для всех тикеров

**OI Divergence — не зависит от ТФ** 
- OI данные — фиксированной частоты, resampling не создаёт новых сигналов
- n=570 для RI на всех ТФ одинаковое
- Оставлен 5m

**Mean Reversion — AF выигрывает от смены ТФ 🔥**
- **AF: 15m h=12 → 62.1%** vs 52.4% на 5m (+13 процентных пунктов!)
- NM: 5m остаётся лучшим (57.6%)

### Изменения в production
- `__init__.py`: AF → `tf: '15m', horizon: 12`
- `cron_scanner.py`: добавлен per-ticker resampling для Reversion (как для VS)
- `scripts/tf_sweep_all.py`: скрипт для повтора sweep

## Состав демо (финальный)

| # | Стратегия | Тикеры | TF | Entry | 
|:-:|:----------|:-------|:--:|:-----:|
| 1 | **Order Block** | 16 tickers (7+9) | H1 | Limit OB level |
| 2 | **VWAP Deviation** | GZ, SR, Si, MC | 5m | Limit extreme |
| 3 | **Mean Reversion** | NM(5m), **AF(15m)** | per-ticker | Limit extreme |
| 4 | **OI Divergence** | RI, GL, Si | 5m | Limit extreme |
| 5 | **Volume Surge** | HS(5m), DX(15m), HY(H1), BM(H1) | per-ticker | Limit extreme |

Всего: ~28 активных тикеров, 5 стратегий, 3 разных ТФ.

## Файлы
- `scripts/tf_sweep_all.py` — скрипт TF sweep
- `docs/plans/tf_sweep_results/tf_sweep.csv` — полные результаты

## Что дальше
- [ ] Наблюдать первые сигналы в демо
- [ ] Мониторинг: Si пересекается в VWAP + OI + OB
