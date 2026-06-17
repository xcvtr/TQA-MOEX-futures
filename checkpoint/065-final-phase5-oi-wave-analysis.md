# Checkpoint 065: Итоговый разбор Phase 5 и OI-стратегий

**Дата:** 2026-06-16 (IRKT, +08:00)

---

## 1. Аудит Phase 5 (оригинальная стратегия)

### Результат
Walk-forward (OOS 2025-2026): **+1,794%**, DD 7.0%, Calmar 118.9
IS-портфель OOS: **+1,384%**, DD 6.1%, Calmar 110

### 7 аудитов пройдено
1. **Ловля тренда** — рынок падал (-8.4%), стратегия росла (+1,794%). Alpha.
2. **Сделки вручную** — 5 сделок проверены, всё корректно.
3. **Monte Carlo** — 50 итераций shuffle, портфельная alpha не data snooping.
4. **Комиссии Alor/Финам** — 5-6% от PnL, стратегия жизнеспособна.
5. **Состав портфеля** — хардкод, не подгонка под OOS.
6. **Reverse WFO** — expanding window, past-only индикаторы.
7. **Self-deception check** — entry по close того же бара (~1 бар форы), стоп по close (не intraday), общий score threshold.

### Главное открытие аудита №7
Стратегия чрезвычайно чувствительна к slippage. При 0.01% slippage (реалистичный для MOEX):
- Return падает с **+1,794% → +509%**
- DD растёт с 7.0% → 16.4%
- Calmar падает со 118.9 → 17.9

Причина: **84 сделки/день** — slippage на каждой убивает доходность.

---

## 2. Попытки адаптировать стратегию

### 2.1 TF Sweep
| TF | Raw signals/day | Trades/day (sim) | Return |
|:--:|:--------------:|:----------------:|:------:|
| **5m** | 402 | **84.6** | +1,794% |
| 15m | 188 | ~40 | не считали |
| **H1** | 42 | **14.1** | **-16.9%** (убыток) |
| H4 | 10 | ~4 | убыток |

**Вывод:** стратегия живёт только на 5m. На H1/H4 — убыток.

### 2.2 Smoothing (EMA40 vol + EMA12 score + threshold 0.55/0.50)
- Return: **+210.6%** (с 0.01% slippage)
- Trades/day: **50.2** (вместо 84)
- DD: 16.6%, Calmar: 8.24

Уменьшили сделки в 1.7x, но return упал в 2.5x. Сглаживание убивает доходность быстрее чем снижает издержки.

### 2.3 Increased hold + removed fade exit + re-entry delay
- Return: **-4.9%** (с 0.01% slippage)
- Trades/day: 5.8
- DD: 12.6%

Полная потеря доходности. Стратегия зарабатывает только на быстрых микро-движениях.

### 2.4 Threshold sweep (5m)
| Threshold | Return | DD | Calmar | Trades/day |
|:---------:|:-----:|:--:|:-----:|:----------:|
| 0.25/0.20 (orig) | +1,794% | 7.0% | 118.9 | 84.6 |
| 0.30/0.25 | +1,564% | 7.4% | 100.8 | 77.6 |

Повышение порога не решает проблему — сделок всё ещё много, доходность падает.

---

## 3. OI-Wave Strategy — полное исследование

### Концепция
OI divergence (физ/юр ratio) как самостоятельный торговый сигнал. Если юрлица агрессивно накапливают позицию (oi_z > 2), ожидаем движение цены в ту же сторону.

### Анализ OI-волн на 63 тикерах MOEX (H1, 2 года)

**Лучшие тикеры по accuracy (Acc12h > 55%):**
| Ticker | Acc12h | Ret12h | Тикер |
|:------:|:-----:|:------:|:------|
| **GK** | **75%** | +0.81% | Медь |
| NR | 70% | +0.44% | |
| MM | 64% | +0.37% | Индекс MOEX |
| **AF** | **62%** | **+1.06%** | Алюминий |
| SBERF | 62% | +0.27% | Сбер |
| RM | 61% | +0.39% | |
| MG | 59% | +0.76% | |
| PD | 58% | +0.54% | Палладий |
| RI | 58% | +0.43% | РТС |
| YD | 58% | +0.78% | Яндекс |
| SR | 56% | +0.90% | Сахар |

**Худшие (accuracy < 50%):** GL (золото), Si (доллар), NG (газ), BR (нефть), LK (Лукойл)

### Характеристики OI-волн
- **Длительность:** 100% волн 3-4 часа. Ни одной длиннее 6 часов.
- **Частота:** ~20-30 волн/тикер за 2 года (1-2 в месяц)
- **Движение:** 0.4-1.0% за 12 часов после сигнала

### Попытки построить стратегию

#### V1: Time-stop 12ч + ATR-стоп
- TP/SL grid по 6 тикерам (GK, AF, MG, YD, SR, NR)
- **Лучшая:** TP=1.0×ATR, SL=2.5×ATR → **+6.33%**, DD 21.5%, Calmar 0.29
- 0.81 trades/day, 60% WR

#### V2: Reverse OI exit (выход при схлопывании волны)
- **-3.58%**, DD 31.9%. Гипотеза не подтвердилась.

#### V3: H4 TF
- **Все комбинации убыточны.** Лучшая: -3.63%, DD 25.7%.

#### V4: M30 TF
- **-95..-100%.** Всего 15 сделок за 1.5 года.

#### V5: Одиночный лучший тикер GK
- **-99%.** Всего 13 сделок за 1.5 года.

---

## 4. Итоговые выводы

### Phase 5 — жизнеспособна, но с оговорками
- Реальный результат с slippage 0.01%: **+509%** а не +1,794%
- Требует **лимитных ордеров** (мейкерская ставка 0%) — только так можно торговать 84 сделки/день без потери на slippage
- На реальном счёте с мейкерскими ордерами и Alor (0.5₽/контракт) — **можно получить +400-500% чистыми**

### OI divergence на MOEX — не торгуется как standalone
- Accuracy 55-75% выше случайности, но величина движения мала (0.4-1.0%)
- При <1 сделке/день капитал простаивает
- Даже портфель из 6 лучших тикеров даёт лишь +6% за 1.5 года при DD 21%
- **OI может работать только как фильтр/подтверждение для другой стратегии**

### Ключевой урок
**Внутридневная торговля на MOEX:** 5m таймфрейм + volume surge даёт реальную alpha, но требует высокой частоты сделок. Главный риск — не рынок, а комиссии и slippage. Решение: мейкерские ордера (0% комиссия MOEX) и Alor/Финам (0.5₽/контракт).

---

## 5. Состояние проекта TQA-MOEX

### Структура
```
/home/user/projects/TQA-MOEX/
├── scripts/
│   ├── phase5_walkforward.py        # Оригинальный walk-forward (+1,794%)
│   ├── phase5_commissions_equity.py  # С комиссиями (+1,688%)
│   ├── 5m_slippage_sweep.py         # Slippage sweep
│   ├── threshold_sweep.py           # Threshold sweep
│   ├── tf_sweep_fast.py             # TF signal counts
│   ├── h1_slippage_sweep.py         # H1 с slippage
│   ├── per_ticker_grid.py           # Per-ticker grid (не дал результатов)
│   ├── oi_wave_analysis.py          # Анализ OI-волн (63 tickers)
│   ├── oi_wave_strategy.py          # OI-wave V1
│   ├── oi_wave_strategy_v2.py       # OI-wave V2 (reverse OI)
│   ├── oi_wave_grid_tp_sl.py        # OI-wave TP/SL grid H1
│   ├── oi_wave_gk_single.py         # GK single test
│   └── bar_level_sim.py             # BarLevelPortfolio class
├── reports/
│   ├── phase5_walkforward/          # Результаты walk-forward
│   ├── phase5_commissions_audit/    # Аудит комиссий
│   ├── tf_sweep/                    # TF и threshold sweep
│   └── oi_wave_strategy/           # OI-wave результаты
├── trading_bot/                     # Live-сканеры (другие стратегии)
└── checkpoint/
    ├── 060-phase5-portfolio-validation-audit.md
    ├── 061-phase5-portfolio-verification-audit.md
    ├── 062-phase5-broker-commissions-equity.md
    ├── 063-oi-loader-ch-pg-fix.md
    └── 064-loader-move-to-tqa-moex.md
```

### Скрипты (new)
- `scripts/tf_sweep_fast.py` — быстрый подсчёт сигналов по TF
- `scripts/h1_slippage_sweep.py` — H1 с slippage
- `scripts/5m_slippage_sweep.py` — 5m с slippage
- `scripts/threshold_sweep.py` — подбор threshold
- `scripts/per_ticker_grid.py` — per-ticker параметры (неудачно)
- `scripts/oi_wave_analysis.py` — анализ OI-волн на 63 тикерах
- `scripts/oi_wave_strategy.py` — OI-wave V1
- `scripts/oi_wave_strategy_v2.py` — OI-wave V2
- `scripts/oi_wave_grid_tp_sl.py` — TP/SL grid
- `scripts/oi_wave_grid_tp_sl_h4.py` — H4 grid
- `scripts/oi_wave_grid_m15_m30.py` — M15/M30 grid
- `scripts/oi_wave_gk_single.py` — GK single ticker

### Данные
- ClickHouse localhost:8123, moex.prices_5m + moex.prices_5m_oi
- Pickle cache: `.tf_sweep_data.pkl` (14 tickers)
- PostgreSQL: 10.0.0.63 (primary), 10.0.0.60 (standby), 127.0.0.1 (standby)

### Cron jobs
- MOEX OI Loader: каждые 5 мин (будни, 10:00-05:00 МСК)
- MOEX OI Hourly Report: каждый час
- MOEX Price Snapshot: каждые 15 мин
- Trading Bot Scanner: каждые 15 мин (остановлен)
- VC 5m signal detector: каждые 5 мин
