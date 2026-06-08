# Checkpoint 015 — Limit Order Retest: All 4 strategies × 2 years

## Что сделано

### 1. Переделка 4 стратегий на лимитные ордера
OpenCode создал `_limit` функции для каждой стратегии:
- `engine.py` → `detect_signals_limit()`
- `reversion_engine.py` → `detect_mean_reversion_signals_limit()`
- `vwap_engine.py` → `detect_vwap_signals_limit()`
- `new_strategies.py` → `detect_oi_divergence_signals_limit()`

Логика единая: после триггерного бара i, лимитный ордер на экстремуме (low[i] для LONG, high[i] для SHORT), ожидание fill в limit_lookback=5 баров, выход через horizon после fill.

### 2. 2-летний бэктест (2024-2026, ~279K сигналов)
`scripts/limit_retest_all.py` — сравнение market vs limit на 2 годах данных.

### 3. Результаты

| Стратегия | Сигналов | Market WR | Limit WR | ΔWR | Вердикт |
|:----------|:--------:|:---------:|:--------:|:---:|:-------:|
| **VWAP Deviation** | **279K** | 52.9% | **53.2%** | **+0.3%** | ✅ Limit |
| **Mean Reversion** | 1K | 52.4% | **55.3%** | **+2.9%** | ✅ Limit |
| **OI Divergence** | 3.4K | 57.0% | 56.9% | -0.1% | ⚖️ Neutral |
| **Volume Surge** | 1K | 47.4% | 49.7% | **+2.2%** | ⚠️ Limit helps, но WR<50% |

**Ключевые находки:**
- VWAP: SR (+1.1%), MC (+1.6%) — limit уверенно лучше. Eu (-1.7%) — market лучше, возможно отключить.
- Mean Reversion: NM h=6: +6.7% 🚀, AF h=6: +5.9% 🚀 — **сильнейший прирост**.
- OI Divergence: RI +1.9-4.6% — limit сильно лучше. GL/Si — нейтрально.
- Volume Surge: HS +7% (!), BM +5.7% — limit даёт рост, но стратегия в целом слабая (<50% WR).

## Файлы
- `scripts/limit_retest_all.py` — бэктест всех стратегий
- `docs/plans/limit_retest_results/market_vs_limit_comparison.csv` — полные результаты
- `trading_bot/engine.py` — + `detect_signals_limit()`
- `trading_bot/reversion_engine.py` — + `detect_mean_reversion_signals_limit()`
- `trading_bot/vwap_engine.py` — + `detect_vwap_signals_limit()`
- `trading_bot/new_strategies.py` — + `detect_oi_divergence_signals_limit()`
- `trading_bot/__init__.py` — + `limit_lookback: 5` во все конфиги
- `trading_bot/cron_scanner.py` — импортированы `_limit` функции (не активны)

## Что дальше
- [ ] Интегрировать limit entry в production (переключить cron_scanner.py на _limit функции)
- [ ] Или выборочно: VWAP + Mean Reversion сначала
- [ ] Volume Surge — пересмотреть стратегию отдельно (WR<50% даже с limit)
