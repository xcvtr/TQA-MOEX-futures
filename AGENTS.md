# TQA-MOEX-futures

**Последний чекпойнт: 207 (2026-08-02)** — OI: BR+NG+SV+RN, risk 30%, +1,364% ROI, MTM DD 10.9% (КСУР-ПГО ГО, ролл-фильтр, размазывание K=400)

## 🚨 Правила работы

1. **Линтер обязателен** — после каждого изменения .py/.json/.yaml/.toml проверять синтаксис
2. **Дважды проверять перед отчётом** — файл создан, скрипт работает, данные свежие, cron жив
3. **Не гадать** — верифицировать прямым запросом
4. **Никогда не смешивать стратегии на одном тикере без приоритета по score**

## 🏆 Чемпионы по Calmar (MTM DD ≤ 20%)

| Стратегия | Тикер | Detect | Risk | ROI | PF | **MTM DD** | **Calmar** |
|:----------|:------|:------:|:----:|:---:|:--:|:----------:|:----------:|
| 🛑 **SH** | **RN** 🆕🏆 | **1m** | **5%** | **+3,024K** | **10.22** | **~5%** | **~200** |
| 🐉 **Dragon** | **GD** 🆕 | **10m** | **7%** | **+735K** | **3.59** | **~7%** | **~50** |
| ⚡ **IR** | **Si** | **1m** | **3%** | **+744K** | **2.90** | **~6%** | **~40** |
| 🐉 **Dragon** | **NG** 🆕 | **3m** | **7%** | **+264K** | **2.45** | **~2%** | **~50** |
| 🐉 **Dragon** | **MM** | **5m** | **5%** | **+18K** | **2.22** | **~4%** | **~5** |

**Полный sweep:** `checkpoint/new-portfolio-results.md`

## 🏆 Финальный портфель (верифицирован 30.07.2026)

| Стратегия | Тикер | Detect | Риск | ROI | PF | MDD |
|:----------|:------|:------:|:----:|:---:|:--:|:---:|
| 🎯 **OI** | **BR** | 10-18 MSK | 30% | **+877K** | — | 59% |
| 🎯 **OI** | **NG** | 10-18 MSK | 30% | **+317K** | — | 52% |
| 🎯 **OI** | **SV** | 10-18 MSK | 30% | **+286K** | — | 51% |
| 🎯 **OI** | **RN** | 10-18 MSK | 30% | **+159K** | — | 57% |
| **ПОРТФЕЛЬ OI** | **4 тикера** | **физ продают→long, 60 мин, ролл-фильтр** | 30% | **+1,364%** | — | **10.9%** ✅ |

## 🚨 Баги исправлены (199)
1. **Look-ahead Dragon** — bars_list: dh + [db] (вход по close текущего)
2. **Cooldown IR** — устанавливается после сигнала (не было — дубликаты)
3. **Повторные сигналы Dragon** — cd_until = db_idx + 24
4. **Live = бэктест** — M1 + resample detect + risk sizing

## Аудит ✅
- Комиссия: round-trip 8₽ (TC=8)
- Trend filter: без look-ahead (m1[:i])
- SH/IR detect: lo_hist/close_hist без текущего бара
- Score: у всех стратегий есть score для multi-strategy
- GO лимит: `ct = min(ct_risk, ct_go)`
- MOEX часы: 15:00-23:45 IRKT, только будни**

---

## 🏆 Сводка всех стратегий (365 дней, common pool, trend filter)

### 🐉 Dragon — MM (detect 3-min), GZ (detect 5-min)

| Риск | MM ROI | MM PF | MM MDD | GZ ROI | GZ PF | GZ MDD | **Портфель ROI** | **MDD** |
|:----:|:------:|:-----:|:------:|:-----:|:-----:|:------:|:----------------:|:-------:|
| **2%** | **+363%** | **2.10** | **19.7%** | **+156%** | **1.66** | **13.0%** | **~+500%** | **~20%** |
| 1% | +119% | 2.29 | 12% | +31% | 1.24 | 15% | +150% | 15% |

### ⚡ Impulse Return — Si (detect M1)

| Риск | ROI | WR | **PF** | **MDD** |
|:----:|:---:|:--:|:------:|:-------:|
| **1%** | **+669%** | **61.5%** | **3.19** | **9.6%** |
| **1.5%** | **~+1000%** | ~60% | ~3.0 | **~15%** |
| 2% | ~+1300% | ~60% | ~2.8 | ~20% |

### 🛑 Stop Hunt — GD (1-min detect, TRIZ) 🔥

| Detect | lb/rt | Риск | GD ROI | GD PF | GD MDD | RN ROI | RN PF | RN MDD |
|:------:|:-----:|:----:|:------:|:-----:|:------:|:------:|:-----:|:------:|
| **1-min** 🏆 | 60/0.05 | **1%** | **+508%** | **2.40** | **9.8%** |
| **1-min** | 60/0.05 | 1.5% | **+1572%** | 2.53 | 17.4% |
| **1-min** | 60/0.1 | 2% | **+1900%** | 2.45 | 19.5% |
| 5-min | 80/0.1 | 2% | +33.7% | 2.56 | 5.1% | RN |
| 5-min | 80/0.05 | 1.5% | +24.7% | 2.50 | 3.8% | RN |

---

## 🧠 TRIZ-улучшения (ключевые находки)

### 1. Trend filter (SMA 50)
```
trend = sma_50(price)
if trend == uptrend: only LONG
if trend == downtrend: only SHORT
```
Эффект: **PF растёт в 1.5x**, MDD снижается в 1.5-2x

### 2. Detect ≠ Tick
- **Detect**: resample M1 → 3/5/10/15-min бары (свой период для каждого тикера)
- **Tick/SL/TP**: на M1 (каждую минуту)
- Эффект: сигналы качественнее, шум M1 отфильтрован

### 3. Common pool vs Per-symbol
| Режим | Доходность | MDD |
|:------|:---------:|:---:|
| **Common pool** 🏆 | **+519.7%** | **19.7%** |
| Per-symbol | +274.3% | 19.7% |

Common pool лучше т.к. сигналы редко пересекаются

### 4. Per-symbol detect period
| Тикер | Стратегия | Detect период |
|:------|:----------|:------------:|
| **MM** | Dragon | **3-min** 🏆 |
| **GZ** | Dragon | **5-min** |
| **Si** | Impulse Return | **M1** |
| **GD** | Stop Hunt | **5-min** |
| **RN** | Stop Hunt | **5-min** |

### 5. Оптимальный риск
| Цель | Риск | Ожидаемая доходность |
|:-----|:----:|:-------------------:|
| MDD ≤ 20% | 1-2% | ~300-700% |
| MDD ≤ 30% | 2-3% | ~500-1500% |
| 1000%+ goal | 2% (IR Si) + 1% (Dragon MM) | ~1000-1500% |

---

## 🏆 Финальный портфель

| Стратегия | Тикер | Detect | Риск | ROI | PF | MDD |
|:----------|:------|:------|:----:|:---:|:--:|:---:|
| **IR** ⚡ | Si | M1 | **1.5%** | **~1000%** | **3.0** | **~15%** |
| **Dragon** 🐉 | MM | 3-min | 1-2% | +363% | 2.10 | 19.7% |
| **Dragon** 🐉 | GZ | 5-min | 1-2% | +156% | 1.66 | 13.0% |
| **SH** 🛑 | GD | 5-min | 1-2% | +47% | 1.46 | 6.1% |
| **SH** 🛑 | RN | 5-min | 1-2% | +23% | 1.62 | 10.3% |

**Общая оценка портфеля: ~1000-1500% годовых, MDD ~20%** ✅

---

## 📁 Данные и артефакты

### ClickHouse (10.0.0.60:8123, db=moex)
- `moex.mt5_continuous` — M1 continuous + ALLFUT (33 контракта, 2024-2026)
- `moex.mt5_bars` — M1 бары (запасной)
- `moex.futoi` — OI fiz/yur (5min, свежие, loader починен 01.08)
- `moex.prices_5m_oi` — OI fiz/yur (M5, до мая 2026)
- `moex.openinterest` — OI + accounts (до июня 2026)
- `moex.tradestats_fo` — AlgoPack trade stats

### PostgreSQL (10.0.0.60:5432, db=moex, schema futures)
- `futures.ticker_specs` — GO, ms, sp, fee_entry (обновляемы через update_go_ksur_pgo.py)
- `futures.portfolio` — конфиг портфеля
- `futures.paper_state*` — состояние paper trader

### Этапы отбора портфеля (checkpoint 190)

**1. Dragon sweep:** `strategies/dragon/scripts/dragon_full_sweep.py`
   → `reports/sweep/dragon_full_sweep_results.json` + `reports/sweep/dragon_full_sweep_output.txt`

**2. TRIZ фильтры:** `strategies/dragon/scripts/dragon_triz_test.py`
   → вывод в stdout (перезапустить при необходимости)

**3. SH RN:** `strategies/dragon/scripts/sh_rn_sweep.py`
   → вывод в stdout

**4. Портфель:** `strategies/dragon/scripts/portfolio_run.py`
   → `reports/sweep/equity_curve.json` + `reports/sweep/equity_curve.html`

**5. OI анализ:** `strategies/dragon/scripts/oi_analysis.py`
   → `reports/sweep/oi_analysis.txt`

**6. GO update:** `scripts/update_go_ksur_pgo.py`
   → PG `futures.ticker_specs.go`

**7. Аудит:** `strategies/dragon/scripts/portfolio_audit.py`
   → вывод в stdout

### Чекпойнты
- `checkpoint/chkpt-190-realistic-slippage.md` — финальный
- `checkpoint/chkpt-189-final-v4.md` — per-ticker fees
- `checkpoint/chkpt-188-final-portfolio.md` — первый финальный
- Все чекпойнты: `checkpoint/` + Obsidian `~/obsidian/Projects/TQA-MOEX-futures/`

### 🔧 Принципы
1. **Detect ≠ Tick** — detect на resample, tick на M1
2. **Реинвест** — risk % от капитала
3. **PnL:** `(exit - entry) / min_step * step_price - commission`, **без `*lot`**
4. **Timezone:** IRKT (+08), MOEX 10:00-18:45 MSK = 15:00-23:45 IRK

### ⏸ Отключено
- CVD, Churn, Lunch Reversal — нет edge
- CR — убыточен для SH

### 🤖 Paper Trader (общий фреймворк, единый крон)
- **Крон (общий на фреймворк):** `*/5 15-23,0-4 * * 1-5` → `~/.hermes/scripts/run_moex_paper_trader.sh` → `run_paper_trader.py --stdout` (без --strategy — ВСЕ enabled из PG)
- **OI (единственная enabled):** `strategies/oi/prod/engine.py` (плагин) + `fetch_day_net()` в common. **BR/NG/SV/RN**, thr -3%, long 60 мин, **risk 30%**, trailing/SL отключены (0.99). **+1,364% ROI, MTM DD 10.9%** (КСУР-ПГО ГО, ролл-фильтр, размазывание по стакану K=400)
- **Dragon/IR: ОТКЛЮЧЕНЫ** (02.08) — cron убран, `futures.portfolio.enabled=false` до разбирательства. ⚠️ Проверять enabled после работы других агентов!
- **Legacy state сброшен** (02.08) — старые позиции Si IR / GD Dragon удалены из futures.paper_state
- **Старый самописный `strategies/oi/paper_trader.py` оставлен** (не используется, для сравнения)
- **OI loader:** `loader.py` → `moex.futoi`, cron `*/5 15-23` + `30 10` (задержка ~5 мин, прогон 70с/64 тикера)
- **ГО обновление:** `scripts/update_go_ksur_pgo.py`, cron `30 6` (MOEX go.xml + FINAM XLS, формула medium×КСУР)
- **Дашборд:** `dashboard.py` — порт 8085
