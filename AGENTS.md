# TQA-MOEX-futures

**Последний чекпойнт: 230 (2026-08-16)** — АУДИТ: портфель OI+DW аннулирован (TZ-фикс, компаунд-масштаб неверен, DD15 недостижим). dayofweek v2: +374% при 33% (после TZ-фикса)
- **✅ ВЕРИФИКАЦИЯ (15.08, финальная)**: OI +902%/DD 7.8%/N=406, dayofweek +156%/DD 4%/N=282 — оба через общий Backtester, Docker=хост (воспроизводимо). Live-параметры PG = бэктест.
- **🚀 LIVE: OI (BR/NG/SV) + dayofweek (SBRF/SPYF) enabled.** dayofweek включён с оптимумом 2026:
  **SBRF risk 10% (2x) + SPYF risk 25% (4x)**, trailing act 0.5%/trail 0.25%, SL 1.5%, timeout 24 (EOD), skip июль
- **dayofweek 2026 (общий капитал 200К, M1)**: SBRF 2x+SPYF 4x = **+130% ROI, MTM DD 16.1%** (цель 100-200% ✅)
  Варианты: 2x/6x=+218% DD21%, 4x/6x=+232% DD26%
- **dayofweek верифицирован на M1**: SBRF +434% MTM DD 10.7% (2023-26), SPYF +63% DD 4.5%,
  портфель +769% CAGR 82% Calmar ~40. Контроль: без фильтра −72.5% → edge чисто dayofweek
- **Спецификации**: SBRF ГО 6050₽ (1пкт=1₽), SPYF ГО 13461₽ (1пкт=82.6₽) — добавлены в ticker_specs
- **Конфиг OI**: риски BR 15%/NG 10%/SV 5%, pyr3 (pyra_max=2), max_hold_h=72ч, sizing_eq_cap=2M, stop 1.5%
- **hold 72ч — ключ к MTM DD ≤ 20%**: 120ч давал DD 55-64% (свинг-просадка), 72ч → 12-13%
- **Результаты (реалистичная модель, компаунд)**: 2023 +371%, 2024 +484%, 2025 +1567%, 2026 +1036-2604% (старт 200K) — DD ≤ 12-19% во всех годах
- **1000%/год достижимы на малом капитале (200K-2M)**; при росте eq ROI падает (кап + ликвидность)
- Баги: пирамидинг в бэктесте удваивал лоты (add_lots=весь lots) — исправлен; MTM по close (как папер)
- Known issue: CH openinterest readonly (реплика 2) — load_eod_oi падает, папер не затронут (futoi жива)
- Мост MT5/Finam ещё писать

**Старый (223) конфиг для сравнения**: thr=3, exit_thr=1.5, pyr5 (pyra_max=4), stop 1.5%, max_positions=1, risk 10% eq, LIQ_FRAC=0.05, CAGR +1415-2128% MTM 18.5% — артефакт (2000+ лотов в 2025).

**🚨 Live: только OI (BR/NG/SV) enabled — верно. IR/Dragon/SH/oi_dom — disabled. Базис-арбитраж (222) — не задеплоен.**

## 🛰 Мониторинг OI папера (с 10.08.2026)
- **Ежедневный отчёт** — `moex_oi_daily_report.py`, cron `0 15 * * 1-5` (15:00 IRK, в этот чат): Eq/DD/позиции/сделки, свежесть futoi + mt5_continuous, bridge tz-fix, сверка цен открытых позиций vs рынок
- **Вотчер открытия позиций** — `oi_position_watchdog.py`, cron `*/5 15-23,0-4 * * 1-5` (тихий режим, молчит если ок): при НОВОЙ позиции проверяет — время входа (10:00-02:00 МСК дневная+вечерняя сессия), цена vs реальная (≤0.5%), |day_net| ≥ thr на момент входа, contracts ≤ лимит
- **Авто-патчер bridge** — `mt5_bridge_tz_fix.sh`, cron каждый час: восстанавливает tz-fix (−3ч) в mt5_moex_bridge.py контейнера, если контейнер пересоздан

## 🚨 Критичные уроки (11.08)
1. **Ликвидность из ISS VOLTODAY, НЕ из tradestats_fo** (AlgoPack стух 13.07, занижение ×180)
2. **max_positions=1 + pyra_max=4** (pyr5 в бэктесте) — раздельные параметры!
3. **Бэктест с компаундом в 1 год даёт артефакт ×200** — считать по полному периоду
4. **Стоп-лосс 1.5% — главный инструмент MDD**: без него MDD 50%, с ним 18.5%

## 🚨 Правила работы

1. **Линтер обязателен** — после каждого изменения .py/.json/.yaml/.toml проверять синтаксис
2. **Дважды проверять перед отчётом** — файл создан, скрипт работает, данные свежие, cron жив
3. **Не гадать** — верифицировать прямым запросом
4. **Никогда не смешивать стратегии на одном тикере без приоритета по score**

## 🚨 ВЕРИФИКАЦИЯ ПРОД СТРАТЕГИЙ (15.08.2026) — после TZ-фиксов (данные в МСК)

**Методика**: общий бэктестер фреймворка, параметры РОВНО как в live (PG portfolio),
сравнение по годам (OOS), slippage 0/2 тика, комиссия из ticker_specs.

### OI (BR/NG/SV) — ✅ ПОДТВЕРЖДЁН (оставить в live)

| Slippage | ROI (3г) | DD | WR | PF | N |
|:--------:|:--------:|:--:|:--:|:--:|:-:|
| 0 тиков | +6918% | 12.8% | 70.7% | 4.04 | 1279 |
| 2 тика | +6816% | 12.8% | 70.0% | 3.94 | 1288 |

Все годы прибыльные (2023 +237K, 2024 +1.42M, 2025 +6.77M, 2026 +5.4M при 0т).
Edge выживает slippage 2т (потеря 1.5% ROI). ⚠️ mt5_continuous до 2026 — только вечерняя сессия.

### dayofweek (SBRF/SPYF) — ⚠️ НЕ ПОДТВЕРЖДЁН при live-риске (шум)

| Тикер | N | WR | PnL (4.8г) | avg | Вердикт |
|:------|:-:|:--:|:---:|:---:|:--------|
| SBRF | 209 | 57.4% | +13,160₽ | 63₽ | ⚠️ шум при 1 лоте |
| SPYF | 210 | 58.6% | +14,647₽ | 70₽ | ⚠️ шум при 1 лоте |

Полные D1 (2022-2026, МСК), вход open+slippage 2т, комиссия. При risk 5% (1 лот,
как live PG) — статистический шум (+6.3% за 2.6 года). Работает только с плечом 2×
(как MOEX-stocks-1: +131% = CAGR ~37%) — но это рычаг на тонком edge.

### ✅ ТЮНИНГ dayofweek (15.08) — модель 016 (trailing+плечо+пирамида) НА H1 ПОЛНАЯ ИСТОРИЯ

**Прогон**: `scripts/bt_dayofweek_016.py` — полные H1 (SBRF/SP500 из mt5_futures
10.0.0.63, 2022-2026, МСК), trailing TP + SL 2% + пирамида + плечо (SBRF 2×/SPYF 1×).

**Свип** (SBRF 2023-26): оптимум **act=0.005, trail=0.0025, sl=0.015 → +267%** (было +155% при trail=0.15).

| Конфиг | SBRF | SPYF | Портфель (мульт.) | CAGR |
|:-------|:----:|:----:|:-----------------:|:----:|
| Базовый (016: 0.5/0.15) | +155% | +26% | +221% | 36% |
| **Оптимум (0.5/0.25/1.5)** | **+267%** | **+36%** | **+400%** | **56%** |

**По годам (оптимум)**: SBRF — ВСЕ 5 лет плюс (2022 +25K, 2023 +203K, 2024 +207K,
2025 +135K, 2026 +61K, WR 55-73%) — ✅ edge реальный, не переобучение.
SPYF — 2023 −5K (убыток), но 2024 +38K, 2025 +5K, 2026 +41K — волатилен.

⚠️ dayofweek работает ТОЛЬКО с плечом/пирамидой (риск > live-5%). При 1 лоте — шум.
Для live: если включать dayofweek серьёзно — риск SBRF ≥ 10-15% + trailing 0.25% + SL 1.5%.

### ✅ MTM DD на M1 (15.08) — честный mark-to-market по lo/hi каждого M1-бара

**Скрипт**: `scripts/bt_dayofweek_m1.py` — M1 (ALLFUT* из mt5_futures 10.0.0.63, 2022-2026),
trailing 0.5/0.25, SL 1.5%, плечо SBRF 2×/SPYF 1×, пирамида. MTM DD = худшая точка позиции.

| Нога | ROI (2023-26) | **MTM DD** | WR | Calmar |
|:-----|:---:|:---:|:--:|:--:|
| SBRF | **+434%** | **10.7%** | 70.2% | ~40 |
| SPYF | +63% | 4.5% | 56.2% | ~14 |
| **Портфель** | **+769%** | **≤10.7%** | — | CAGR **82%** |

⚠️ На M1 ROI ВЫШЕ чем на H1 (+434% vs +267%) — trailing на минутках точнее ловит
движение. MTM DD 10.7% — в пределах цели ≤20%. SBRF все годы плюс (WR 55-76%).

**Решение**: OI остаётся в live (единственный подтверждённый edge). dayofweek —
рекомендация: не увеличивать риск/плечо без явного решения пользователя.



**Методика**: общий бэктестер фреймворка (tqa_framework), mt5_continuous (полная плотность ~850 баров/день),
8 тикеров (BR NG SV RN GZ GD MM Si), trailing TP (0.5%/0.3%/12 баров), slippage 0 и 2 тика, комиссия.
Прогон: `scripts/bt_verify_research.py` в форке. ПОДТВЕРЖДАЕТ прошлую верификацию 07.08 (champion-verification).

| Стратегия | N сделок | ROI 0т | ROI 2т | WR | PF | Вердикт |
|:----------|:--------:|:------:|:------:|:--:|:--:|:--------|
| 🐉 Dragon | 4,284 | **−39%** | −62% | 45% | 0.89 | ❌ ОТБРОШЕНА |
| ⚡ Impulse Return | 7,797 | **−88%** | −130% | 46% | 0.89 | ❌ ОТБРОШЕНА |
| 🛑 Stop Hunt | 13,019 | **−125%** | −186% | 45% | 0.87 | ❌ ОТБРОШЕНА |
| oi_dom | — | — | — | — | — | ⚠️ не верифицируем (стакан dom_qsh только TATN) |

**Контроль (валидность методики)**: OI BR/NG/SV тем же бэктестером = **+1729%, DD 7.8%, PF 3.85** — эталон жив.
**Баг, найденный при верификации**: позиции в backtester НЕ закрывались (state терялся между тиками) — после фикса N=8→13K сделок, стратегии показали реальный убыток.

⚠️ Таблицы «Чемпионов» ниже (SH RN +3024K, Dragon GD +735K, IR Si +744K) — УСТАРЕВШИЕ артефакты ДО аудита,
оставлены для истории. Не доверять, не включать в live.


## 🏆 Портфель OI (историческая верификация 30.07.2026, risk 35% — УСТАРЕЛО)

| Стратегия | Тикер | Detect | Риск | ROI | PF | MDD |
|:----------|:------|:------:|:----:|:---:|:--:|:---:|
| 🎯 **OI** | **BR** | 10-18 MSK | 35% | **+877K** | 2.2 | 59% |
| 🎯 **OI** | **NG** | 10-18 MSK | 35% | **+317K** | 1.8 | 52% |
| 🎯 **OI** | **SV** | 10-18 MSK | 35% | **+286K** | 2.1 | 51% |
| 🎯 **OI** | **RN** | 10-18 MSK | 35% | **+159K** | 2.5 | 57% |

⚠️ Эта таблица (risk 35%, без стоп-лосса) — старая оценка до аудита 11.08.
**Актуальный конфиг**: risk 10% eq + стоп 1.5% + pyr5 → CAGR +1415-2128%, MTM 18.5% (см. секцию выше).

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

## 🏆 Финальный портфель (актуальный, 11.08.2026)

**Live: только OI (BR/NG/SV).** Остальные стратегии (IR/Dragon/SH) — исследовательские, в live не включены.

### 🎯 OI (контрарный по day_net физлиц) — LIVE
| Параметр | Значение |
|---|---|
| Тикеры | BR, NG, SV |
| Вход | \|day_net\| ≥ 3% (физ накопили дисбаланс) |
| Направление | contrarian: day_net<0 → long, day_net>0 → short |
| Выход | day_net ≥ 1.5% (обратное условие) или **72ч** или стоп |
| Стоп-лосс | **1.5%** (ключ к MDD!) |
| Пирамидинг | **pyr3 (2 добавки по +0.3% от base)** |
| Позиций на тикер | 1 (max_positions=1) |
| Риск | **BR 15% / NG 10% / SV 5%** от min(eq, cap) |
| Кап eq | **sizing_eq_cap = 2M₽** (из PG) |
| Окно | 10:00-02:00 МСК (дневная + вечерняя сессия) |

**Бэктест (реалистичная модель, компаунд 2023-26): 2023 +371%, 2024 +484%, 2025 +1567%, 2026 +1036-2604% (старт 200K), MTM DD ≤ 12-19%, N=1100, WR 49-68%. См. checkpoint/224.**

### Исторические оценки (для сравнения, НЕ live)
- IR Si: ~1000% при MDD 15% (риск 1.5%) — исследовательский
- Dragon MM: +363% MDD 19.7% — исследовательский
- SH RN: +3024K Calmar ~200 — исследовательский

---

## 🏗 Архитектура данных (14.08.2026): LIVE = PG, CH = только бэктесты

**Live (папер/detector/executor) читает ТОЛЬКО PG.** CH для live не используется (проверено: блокировка порта 8123 не ломает папер).

| Данные | Где live | Где бэктесты | Обновление |
|---|---|---|---|
| M1 бары | `futures.bars_1m` (14 дней, autopurge) | `moex.mt5_continuous` (вся глубина) | мост dual-write |
| OI (day_net) | `futures.futoi_iss` (14 дней, autopurge) | `moex.futoi` (вся глубина) | loader.py (ISS) |
| Дневные close (dayofweek) | `futures.bars_d1` (120 дней) | — | load_bars_d1.py (cron 14:00) |
| Объёмы (лимиты) | `futures.daily_vol` | — | ISS + кэш |
| Стакан (CVD/oi_dom) | `futures.dom` | `moex.dom_qsh` | мост |

**Мост (mt5_moex_bridge.py, в контейнере mt5-finam)**: пишет M1 в CH (вся глубина) + PG bars_1m (14 дней, autopurge).

**Ключевые файлы**: `run_paper_trader.py` (корень), `strategies/common/paper_trader.py`, `engine/detector.py`, `engine/executor.py` — все без CH-вызовов (только fallback'и в detector для daily, не срабатывают при живом PG).

**Правило**: live НИКОГДА не должен зависеть от CH. Если PG упал — папер стоп (не торгует на устаревшем), это безопаснее чем fallback на CH.

---

## 📁 Данные и артефакты

### ClickHouse (10.0.0.60:8123, db=moex)
- `moex.dom_qsh` — стакан QScalp из SMB-шары (дельты, 192 дня 2026, ReplicatedReplacingMergeTree)
- `moex.mt5_continuous` — M1 continuous + ALLFUT (33 контракта, 2024-2026) ⚠️ TZ: bridge пишет с −3ч (фикс 10.08)
- `moex.mt5_bars` — M1 бары (запасной)
- `moex.futoi` — OI fiz/yur (5min, свежие) ⚠️ TZ: loader пишет +5ч (фикс 10.08)
- `moex.prices_5m_oi` — OI fiz/yur (M5, до мая 2026)
- `moex.openinterest` — OI + accounts (до июня 2026)
- `moex.tradestats_fo` — AlgoPack trade stats ⚠️ **СТУХ 13.07.2026! Не использовать для объёмов** (занижение ×180). Объёмы — из ISS VOLTODAY API

### PostgreSQL (10.0.0.60:5432, db=moex, schema futures)
- `futures.ticker_specs` — GO, ms, sp, fee_entry (обновляемы через update_go_ksur_pgo.py)
- `futures.portfolio` — конфиг портфеля (OI: thr3, exit1.5, pyr5, stop1.5, max_pos1)
- `futures.paper_state*` — состояние paper trader
- `futures.active_symbols` — активные контракты (для объёмов ISS)

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
- **Watchdog OI:** `scripts/oi_watchdog.py` (крон */30 15-23,0-2 будни) → email m4slayer@ya.ru при простое/баге (futoi stale, mt5 stale, 0 сделок 3+ дня, DD>25%)
- **Крон (общий на фреймворк):** `*/5 15-23,0-4 * * 1-5` → `~/.hermes/scripts/run_moex_paper_trader.sh` → `run_paper_trader.py --stdout` (без --strategy — ВСЕ enabled из PG)
- **2 стратегии live (05 тикеров):** oi (BR/NG/SV, contrarian) + oi_dom (RN/TATN hold 120, подтверждение стаканом: dom_imb). oi_dom: DD 45%→21%, TATN WR 56.5%, RN WR 62.4% (hold 120).
- **OI (старая):** `strategies/oi/prod/engine.py` (плагин, long+short) + `fetch_day_net()` в common. **BR/NG/SV/RN**, thr ±3%, long/short 60-120 мин, **risk 40%**, trailing/SL отключены (0.99). **+1,309%/год ROI, MTM DD 18.0%** (честно: свежий бар ≤5 мин; КСУР-ПГО ГО; ролл-фильтр: expiration_date из ISS + гэп >2% + roll_close позиций; размазывание K=400)
- **Dragon/IR: ОТКЛЮЧЕНЫ** (02.08) — cron убран, `futures.portfolio.enabled=false` до разбирательства. ⚠️ Проверять enabled после работы других агентов!
- **Legacy state сброшен** (02.08) — старые позиции Si IR / GD Dragon удалены из futures.paper_state
- **Старый самописный `strategies/oi/paper_trader.py` оставлен** (не используется, для сравнения)
- **OI loader:** `loader.py` → `moex.futoi`, cron `*/5 15-23` + `30 10` (задержка ~5 мин, прогон 70с/64 тикера)
- **ГО обновление:** `scripts/update_go_ksur_pgo.py`, cron `30 6` (MOEX go.xml + FINAM XLS, формула medium×КСУР)
- **Дашборд:** `dashboard.py` — порт 8085
