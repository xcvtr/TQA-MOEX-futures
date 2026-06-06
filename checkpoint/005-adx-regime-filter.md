# 005 — ADX Regime Filter + Dashboard + Walk-forward

**Дата:** 2026-06-08 04:25
**Статус:** ADX-фильтр внедрён. Dashboard запущен. Walk-forward оптимизатор готов.

---

## Ключевые открытия

### 1. ADX regime filter (фильтр тренда)

ADX (Average Directional Index) измеряет силу тренда. ADX > 20 = тренд, ADX < 20 = боковик.

**Volume Surge в боковике — ложные сигналы.** ADX отсекает их.

### 2. Результаты ADX на рабочих тикерах

| Тикер | Без ADX | С ADX (порог 20) | Эффект |
|:-----:|:-------:|:----------------:|:------:|
| **HS** | 51% WR, PF 0.67 | **69% WR, PF 1.23** | +18% WR ✅ |
| **KC** | 50% WR, PF 1.23 | 20% WR, PF 0.49 | ❌ ADX убивает |
| **DX** | 64% WR, PF 4.64 | 100% WR, n=2 | Слишком мало |
| **HY** | 45% WR, PF 0.79 | **65% WR, PF 1.28** | +20% WR ✅ |

### 3. BM с ADX — прорыв (rescan)

| Параметры | Без ADX | С ADX |
|:---------:|:-------:|:-----:|
| BM 2.0/1.5/h=3 | 52% WR, PF 0.57 | **62.5% WR, PF 3.72** |
| BM 1.5/1.5/h=3 | 48% WR, PF 0.60 | **60.0% WR, PF 3.82** |
| BM 2.5/1.5/h=3 | 54% WR, PF 0.61 | **62.1% WR, PF 3.06** |

**ADX превращает BM из мусора в конфетку.** DD падает с 15% до 1%.

### 4. HS оптимальный ADX порог — 20

| Порог | n | WR% | PF | DD% |
|:-----:|:-:|:---:|:--:|:---:|
| 15 | 43 | 60.5% | 1.10 | 2.4 |
| **20** | **29** | **69.0%** | **1.23** | **2.2** |
| 25 | 16 | 62.5% | 0.76 | 2.3 |
| 30 | 8 | 50.0% | 0.75 | 0.7 |

---

## Система

### Файлы

| Файл | Назначение |
|------|-----------|
| `trading_bot/__init__.py` | Конфиг с ADX-флагами для HS/HY |
| `trading_bot/engine.py` | zs(), detect_signals() — добавлен 'idx' |
| `trading_bot/scanner.py` | load_data(), scan_all(), format_signal() |
| `trading_bot/filters.py` | calc_adx(), add_regime_filter(), calc_roc() |
| `trading_bot/optimizer.py` | Walk-forward с ADX, grid search |
| `trading_bot/tracker.py` | Paper positions, PnL, exits |
| `trading_bot/alerts.py` | Telegram-ready форматтеры |
| `trading_bot/cron_scanner.py` | Entry point с ADX-фильтром |
| `trading_bot/dashboard.py` | HTTP :5080 — Equity, positions, stats |
| `trading_bot/rescan.py` | BM/CC/RN rescan с ADX |

### Dashboard
`http://10.0.0.60:5080` — тёмная тема, equity curve SVG, открытые позиции, сделки

### Cron
`*/15 7-18 * * 1-5` — сканирование каждые 15 мин с ADX-фильтром

---

## Бэктест стратегии (с ADX)

| Тикер | Параметры | n/год | WR% | PF | DD% | %/год на капитал |
|:-----:|:---------:|:-----:|:---:|:--:|:---:|:----------------:|
| **HS** | 2.75/1.5/12, ADX>20 | 50 | **69%** | 1.23 | 2.2% | +35% |
| **BM** | 2.0/1.5/3, ADX>20 | 55 | **63%** | 3.72 | 1.1% | +80% |
| **HY** | 2.5/YUR-DOM, ADX>20 | 30 | **65%** | 1.28 | 5% | +5% |
| **KC** | 2.0/2.0/24, без ADX | 90 | 57% | 1.54 | 7.1% | +38% |
| **DX** | 3.0/1.5/48, без ADX | 61 | 58% | 2.00 | 8% | +13% |

## Дальнейшие шаги

1. Добавить BM в SCAN_SYMBOLS (vol=2.0, div=1.5, h=3, ADX>20)
2. DSPy-оптимизация thresholds
3. Correlation между тикерами
4. Добавить стоп-лосс в engine (не только в tracker)
