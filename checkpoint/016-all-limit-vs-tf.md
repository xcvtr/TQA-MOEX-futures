# Checkpoint 016 — Все стратегии на лимитных ордерах + VS per-ticker TF

## Что сделано

### 1. Все 5 стратегий переведены на limit entry в production
- `cron_scanner.py` — переключены вызовы с detect_signals → detect_signals_limit для VS, Reversion, VWAP, OI Div
- Добавлен per-ticker ресемплинг TF для Volume Surge
- Исправлен баг: `detect_signals_limit(sym, rows, cfg)` → `detect_signals_limit(rows, cfg)` (3 аргумента вместо 2)

### 2. Volume Surge — профессиональное переосмысление

**Диагноз:** 5m TF давал 47-50% WR — монетка. fiz_z ≈ yur_z на MOEX, направление yur_z → dir не предсказывает цену на мелком ТФ.

**Решение:** per-ticker TF настройка:

| Тикер | TF | vol_thresh | horizon | WR (тест) | n |
|:-----:|:--:|:----------:|:-------:|:---------:|:-:|
| **DX** | **15m** | 3.0 | 24 | **62.8%** | 94 |
| **HS** | 5m | 2.5 | 12 | **59.9%** | 292 |
| **HY** | **H1** | 2.0 | 12 | **58.7%** | 235 |
| **BM** | **H1** | 2.0 | 12 | **56.5%** | 92 |
| **KC** | — | — | — | **отключён** (<52%) |

### 3. Прочие изменения
- Eu отключён из VWAP (limit хуже market на -1.7%)
- KC отключён из VS (<52% WR на всех ТФ)
- Все `days` в cron_scanner повышены с 30 до 730
- В __init__.py добавлены `tf` и `limit_lookback` в конфиги

### 4. Состав демо на текущий момент

| # | Стратегия | Тикеры | Entry |
|:-:|:----------|:-------|:-----:|
| 1 | **Order Block (Variant D)** | UC, ED, Si, RM, KC, NA, GD + RI, LK, SBERF, GK, MC, RN, IMOEXF, YD | Limit OB level |
| 2 | **VWAP Deviation** | GZ, SR, Si, MC (Eu OFF) | Limit high/low |
| 3 | **Mean Reversion** | NM, AF | Limit high/low |
| 4 | **OI Divergence** | RI, GL, Si | Limit high/low |
| 5 | **Volume Surge** | HS(5m), DX(15m), HY(H1), BM(H1) — KC OFF | Limit high/low |

## Файлы
- `trading_bot/__init__.py` — per-ticker tf, vol_thresh, hor, KC/Eu disabled
- `trading_bot/cron_scanner.py` — все стратегии на _limit, VS resampling
- `trading_bot/engine.py` — + detect_signals_limit()
- `trading_bot/reversion_engine.py` — + detect_mean_reversion_signals_limit()
- `trading_bot/vwap_engine.py` — + detect_vwap_signals_limit()
- `trading_bot/new_strategies.py` — + detect_oi_divergence_signals_limit()

## Что дальше
- [ ] Наблюдать за сигналами в paper (2 недели)
- [ ] При стабильности — расширить expansion OB (RI, LK, SBERF...)
- [ ] Мониторить пересечения Si (VWAP + OI + OB)
