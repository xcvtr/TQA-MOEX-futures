## [230] 2026-08-15
### Added
- dayofweek v2: доп. сигналы Вт L / Пт S (по prev_week_ret) — N 337→679, стабильны по годам
- dayofweek в live-терминах (risk_pct по ГО): DD 15% = risk 200% → CAGR 175%
- Портфель OI+DW: w_OI=0.3 + DW risk 200% → ROI +1197%, суммарный DD −15.1% (1 год)
### Changed
- strategies/dayofweek/detect.py: +Вт L/Пт S, фикс ночной сессии (hour<7 skip)
- Методика: leverage-скрипт НЕ эквивалентен live risk_pct (eq×risk/ГО)
### Warning
- Портфель требует аудита: 1 год (не 2023-26), data_source OI (bars vs mt5_continuous),
  Backtester DW (44 сделки) vs M1-скрипт (188) — несопоставимы, конкуренция за капитал
- Checkpoint: checkpoint/230-dayofweek-v2-portfolio-15dd.md

## [229] 2026-08-15
### Fixed
- Чекп.228 (базис CAGR 24.5%) АННУЛИРОВАН — D1-модель не учитывала движение фьючерса
### Added
- M1-пересчёт: чистый фьючерс −12% CAGR (DD −105%), связка 6.5% CAGR (DD −28%)
- Свип риска: ROI не растёт с риском (тонкий edge) — MOEX-div закрыт
### Changed
- МЕТОДИКА: M1-пересчёт обязателен после D1-оценки любой стратегии
- Checkpoint: checkpoint/229-basis-m1-recalc-closed.md

## [228] 2026-08-15
### Added
- **Базис-арбитраж фьючерсами**: CAGR 24.5% (2023-26), MTM DD 1.8% — «зачем акции, есть фьючерсы»
- TQA-moex-div-framework (новый проект): верификация MOEX-div через фреймворк
- Watchdog'и: мост (авто-рестарт) + freshness (алерты)
### Verified
- Дивидендный кэпчур НЕ edge: avg −1.7% (2024 −127%, 2025 −43%)
- Их 64-367% — артефакты (ОФЗ-экспонента, без комиссии, неполные данные)
- MT5-дубль на 63 невозможен (нет AVX2); 60=64 один хост
### Fixed
- load_m1_from_ch: query mt5_continuous (потерян при рефакторинге)
### Changed
- PG failover 60→63 (lag=0), watchdog'и в cron
- Checkpoint: checkpoint/228-moex-div-basis-futures-failover.md

## [227] 2026-08-15
### Changed
- **dayofweek включён в live**: SBRF risk 10% (2x), SPYF risk 25% (4x), trailing 0.5/0.25, SL 1.5%, EOD
- Тюнинг 2026: +130% ROI при MTM DD 16.1% (общий капитал 200К, M1)
### Fixed
- trailing_activation/trail в PG были ПРОЦЕНТЫ (0.5=50%), папер ждёт ДОЛИ (0.005) — ломал trailing dayofweek
- numeric(5,3) округлял 0.0025→0.003 → numeric(6,4)
### Added
- ticker_specs: SBRF ГО 6050₽, SPYF ГО 13461₽ (1пкт=82.6₽)
- bt_dayofweek_m1.py — MTM DD на M1 (честный mark-to-market по lo/hi)
- bt_dayofweek_016.py — модель 016 (trailing+плечо+пирамида) на полных H1
### Verified
- Контроль: без dayofweek-фильтра −72.5% → edge чисто dayofweek
- Docker-воркер пересобран с TZ-фиксами: OI +1511% N=395 — идентично хосту
- Checkpoint: checkpoint/227-dayofweek-live-m1-docker.md

## [226] 2026-08-15
### Changed
- **ВСЕ данные в МСК (Europe/Moscow)**: PG timezone (Etc/UTC→МСК), CH timezone (config.d)
- loader: ISS +5ч хак → aware +03:00; мост: naive → aware UTC
- папер: replace(tzinfo) → astimezone ×3; детектор: datetime.now(utc) ×4
- oi_watchdog: IRK +8/+5 → МСК +3, toUnixTimestamp
### Fixed
- futoi_iss в PG был в БУДУЩЕМ на 8ч (влиял на day_net!) — миграция −8ч
- _parse_ts: naive CH-строка = МСК (было IRK → сдвиг −5ч, N=395→521)
- cron update_moex_oi: */5 15-23 → */5 15-23,0-5 (вечерняя сессия не покрывалась)
- run_moex_oi_silent.sh: python3 → venv tqa-moex-futures (не было clickhouse-connect)
### Verified
- CH/PG unix совпадают (bars 19:27, futoi 19:25 МСК)
- day_net детектор = папер (BR 1.708, NG −1.031, SV −0.651)
- OI бэктест: N=395, +1511%, DD 7.8%, WR 68.6%
- Checkpoint: checkpoint/226-tz-fixes-msk.md

## [225] 2026-08-14
### Changed
- **LIVE=PG**: папер/detector/executor читают только PG (bars_1m, futoi_iss, bars_d1, dom, daily_vol). CH — только бэктесты. Мост dual-write (CH вся глубина + PG 14д autopurge). Проверено: live работает без CH
- **Docker-воркер**: общий бэктестер tqa_framework в контейнере (пишет в тест-PG :5433, читает прод-CH)
- **Верификация исслед. стратегий**: Dragon −39%, IR −88%, SH −125% (0 slippage) — ВСЕ ОТБРОШЕНЫ. oi_dom не верифицируем (нет стакана)
### Fixed
- Backtester: позиции не закрывались (state терялся) → N=8 вместо 13K
- Backtester: slippage_ticks добавлен
- detector: day_net не подгружался для OI (добавлен attach_day_net)
- futoi_iss autopurge: 2 мес → 14 дней
### Added
- futures.bars_d1 (дневные close dayofweek) + load_bars_d1.py (cron 14:00)
- futures.daily_vol (кэш объёмов ISS)
- bt_verify_research.py (верификация исслед. стратегий)
- Checkpoint: checkpoint/225-live-pg-docker-worker-verify-research.md

## [224] 2026-08-11
### Changed
- Финальный оптимум OI под MTM DD ≤ 20%: риски BR 15%/NG 10%/SV 5%, pyr3 (pyra_max=2), max_hold_h=72ч, sizing_eq_cap=2M
- hold 72ч — ключ к DD: 120ч давал MTM DD 55-64% (свинг-просадка), 72ч → 12-13%
- SIZING_EQ_CAP читается из PG (было хардкод 10M)
- Баг бэктеста: add_lots = весь lots вместо base_lots (пирамидинг удваивал лоты) — исправлен
- MTM в бэктесте по close (как папер), не по lo/hi
- Cron fix: run_moex_eod_oi.sh → venv tqa-moex-futures (system python3 без requests)
- Known issue: CH openinterest readonly (реплика 2, zookeeper metadata lost) — папер не затронут (futoi жива)
- Checkpoint: checkpoint/224-oi-final-optimum-hold72-cap2m.md

## [223] 2026-08-09
### Fixed
- Live реинвест/компаунд: 3 лимита душили рост лотов (volume cap 0.2×tick_volume → макс 11 лотов BR!)
- load_daily_volumes(): реальные дневные объёмы AlgoPack (контракты) вместо mt5 tick_volume
- TICKER_LIMITS при старте: BR 75083, NG 158978, SV 84614 (было 100/100/80)
- MAX_CONTRACTS 20 → 1000; volume cap = 10% дневного объёма (ёмкость рынка)
- Компаунд теперь работает до ~100M+ (реинвест был: equity+=pnl, contracts=eq×risk/go)
- **ПГО формула: ГО = medium × kpur/ksur** (понижение ~2 раза; было med×ksur — фейк 8.6×)
- PG ГО: Si 11947, USDRUBF 11758, Eu 13897, EURRUBF 13785, CNYRUBF 958, NG 6093, SV 10509
- ROI за год: с ПГО +1801%, без ПГО +494% (MTM 8.1% / 5.3%)
- Проверено: live-трейдер запускается, Eq=200K чисто
- Checkpoint: checkpoint/223-live-compounding-liquidity-fix.md
## [222] 2026-08-09
### Added
- Стратегия базис-арбитража: Si/Eu/CNY ↔ перпетуалы MOEX (USDRUBF/EURRUBF/CNYRUBF)
- Ключевое открытие: КСУР-ГО перпетуалов в 16× меньше полного (USDRUBF 2633 vs 82170)
- Загружены данные перпетуалов: USDRUBF/EURRUBF/CNYRUBF/GLDRUBF/IMOEXF (M1+H1)
- Финал: SHORT-only z>1.5 hold 168 risk 5% → CAGR 55%, DD 13%, Calmar 4.3, N=45
- OOS walk-forward (калибровка 22-23 → тест 24-26): CAGR 77%, DD 14%, WR 100% (N=21)
- AlfaForex отброшен: своп −500/ночь, нет lead-lag (проверено H1/M5/M1)
- Скрипты: scripts/basis_arb/backtest.py + short_only/short_wf/distribution/ksur_sim/leadlag
### Changed
- Критический аудит: исправлены 3 бага (look-ahead базиса, пирамидинг без маржи, MTM по close→lo/hi)
- Портфель после аудита: риск=8% pyr=1 lots≤1000 → CAGR 1550%, MTM 13%, Calmar 119 (лучший по Calmar)
- Пирамидинг потерял привлекательность: pyr=1 лучший (часть добавок была бесплатной маржей)
- Портфельный тест OI+BA (общий капитал): pyr=2 risk=5% lots≤1000 → CAGR 1830%, MTM 17%
- Ликвидность: mt5 vol = tick_volume, реальные объёмы AlgoPack в 10-50× (BR 631K/день)
- Реоптимизатор OI (oi_reopt_monthly): добавлен MTM DD ≤ 20% фильтр в выбор конфигов
- Live-исполнение: решение = MT5 (FINAM) или Finam API, мост ещё писать (iceberg 20-50)
- Checkpoint: checkpoint/222-basis-arbitrage-ksur.md
## [221] 2026-08-09
### Changed
- Критический аудит OI (ТРИЗ-дебаты): все 5 проверок пройдены
- Look-ahead: микро-тайминг (59 сек) НЕ влияет — CAGR 365.2 vs 364.8%
- Монте-Карло: p=0.0000 (знаки) — edge статистически значим
- Per-ticker: все 5 тикеров + LONG/SHORT в плюсе, edge распределён
- Выбросы: max +23K, топ-10 = 13% — нет артефактов
- По годам: 2022-2026 все положительные (2023 WR 79.5%)
- Checkpoint: checkpoint/221-oi-critical-audit-triz.md
## [220] 2026-08-09
### Changed
- Live-деплой OI: fetch_day_net = накопление за день (как бэктест), граница дня 15:00 IRK, окно 72ч
- Выход по ОИ (обратное условие), max_hold_h=120ч, slippage 1 тик на выходе, пирамидинг +0.5%
- Per-ticker лимиты контрактов (BR/NG 100, SV 80, RI 50, TT 30), direction=contrarian (LONG+SHORT)
- КРИТИЧЕСКИЙ АУДИТ: баг пирамидинга ×7 (pnl*=n_parts по цене входа) — исправлен в реоптимизаторе
- Честные цифры: CAGR 330-440% (компаунд микро-капитала), MTM 13-17%, OOS 2022 +856%
- Реоптимизатор (cron 1-го числа): thr4/ex2/risk10% выбрано по Calmar
- PG state сброшен (200K чистый старт в понедельник 10.08)
- Checkpoint: checkpoint/220-oi-live-deploy-critical-audit.md
## [219] 2026-08-08
### Changed
- ГЛАВНЫЙ БАГ исправлен: таймзоны — futoi и mt5_continuous ОБЕ в IRK, скрипты добавляли +5ч к futoi → сигналы уезжали от цен. Фикс: UTC-epoch + день = 15:00 IRK
- OI редкие крупные: thr8, ядро NG/BR/SV/RI/TT, пирамидинг по % цены (0.5%), per-ticker риск 20/15%, компаунд
- Walk-forward: тест 24-26 CAGR ~203%, MDD 7.7%, WR 80.8% — edge подтверждён OOS
- Трейлинг/замок/DD-control — ВРЕДНЫ (режут движение). Пирамидинг по % — КЛЮЧЕВОЙ
- Checkpoint: checkpoint/219-oi-timezone-fix-pyramiding.md
## [218] 2026-08-08
### Changed
- OI-улучшения: LONG-only (шорты убыточны — убраны) + hold 120 (откат 2ч) + риск×1.5 → +5480% / MDD 21.2% (2024-26), Calmar 258 vs 94
- Динамика (DD-control, LQ по |dn|) НЕ помогает — фиксированный вариант лучший
- Vol/сезонные фильтры опровергнуты (см. 217)
- Checkpoint: checkpoint/218-oi-long-only-hold120.md
## [217] 2026-08-08
### Changed
- Верификация всех стратегий: IR Si / SH RN / Dragon GD — артефакты (разреженные mt5_bars + in-sample), OI NG/BR/SV — реальный edge (p=0.0015, OOS 2021-23 плюс)
- Watchdog OI: 2 бага (TZ futoi MSK→IRK +5ч, таблица paper_trades вместо paper_trades_oi)
- Vol-фильтр бэктест: персистентность/сезонность волатильности НЕ улучшает OI (только сезонный риск +9%)
- Новые рынки: US-индексы (NASD/ES) нет edge; US-акции NVDA — слабая затухающая зацепка
- Checkpoint: checkpoint/217-strategy-verification-vol-filter.md
## [203] 2026-08-01
### Added
- OI-стратегия (физ продают → long, hold 120): BR+NG+SV, +1,067%, MDD 13.4%
- scripts/oi/ — oi_basic_test, oi_sweep_all, oi_portfolio_backtest
- Cron OI loader: */20 15-23 + 30 10 (update_moex_oi.sh)
### Fixed
- OI loader: CH 10.0.0.64→10.0.0.60, futoi_iss(readonly)→futoi
- Данные OI обновлены до 2026-07-31
### Removed
- Retest (убыточен на MOEX с честными лимитками)
- Checkpoint: checkpoint/203-oi-strategy-1067pct.md
## [191] 2026-07-26
### Added
- **Dashboard v5:** `dashboard_v5.py` — порт 8085, общий портфель + по стратегиям + floating PnL
- **System cron:** `scripts/cron_paper_v5.sh` → crontab `*/5 15-23,0-4 * * 1-5`
### Changed
- **Paper Trader logic:** per-ticker fees (из PG fee_entry), slippage 2-5 tick, volume cap 20%, max 20 ct, trend filter SMA50
- **Paper Trader:** вынесен из Hermes cron в system crontab
- **Timeout:** 60→120с в run_paper_trader.py
- AGENTS.md: обновлён до чекпойнта 191
### Fixed
- NaN в PnL% дашборда (shares vs contracts)
- Port conflict: dashboard переехал на 8085
### Checkpoint
- checkpoint/chkpt-191-paper-dashboard.md

## [190] 2026-07-26
### Added
- **Реалистичный slippage:** 2-5 tick в зависимости от shares (2 + shares//3)
- **Финальный портфель v5:** +7,721% ROI, Cash MDD 7.09%, MTM MDD 7.92%
- Портфель устойчив — разница с v1 всего 1.1%
### Changed
- AGENTS.md: обновлён до чекпойнта 190
### Checkpoint
- checkpoint/chkpt-190-realistic-slippage.md

## [189] 2026-07-26
### Added
- **Финальный портфель v4:** IR Si 10% + GD 20% + MM 15% + SH RN 20% + NG 20%
  - ROI: +7,849% | Cash MDD: 7.07% | MTM MDD: 7.90% | Trades: 4,331
  - SH RN PF=17.91 — главный драйвер
- **Комиссии per-ticker:** Si=4₽, GD=44₽, MM=2₽, RN=7₽, NG=4₽ (из PG fee_entry)
- **GO КСУР ПГО:** скрипт scripts/update_go_ksur_pgo.py
- **Полный аудит:** look-ahead, multi-contract, PnL, SL/TP, equity curve — чисто
### Changed
- AGENTS.md: обновлён до чекпойнта 189
### Fixed
- O(n²) баг в IR detect (close_hist копировал все бары)
- Commission: было 4₽ для всех, стало per-ticker
- MOEX GO: исправлены с разрозненных уровней на единый КСУР ПГО
### Removed
- GZ, CR, BR — отсев по PF<1.5
- fiz/yur фильтры — не улучшают
- Limit entry — не работает (Dragon PF падает до 0.6)
### Checkpoint
- checkpoint/chkpt-189-final-v4.md

## [168] 2026-07-17
### Added
- Dragon integration into PortfolioEngine: M1→M5 resampling, bars_list
- executor.py parses params JSONB (stop_loss_pct) from PG
- broker.py: configurable slippage_in parameter

### Changed
- engine.py: M1→M5 resampling in signal loop (not every bar)
- engine.py: _build_bar optimized (no heavy bars_list per bar)
- PG futures.portfolio: Dragon trailing params (1.5/0.5/60/1.0)
- PG futures.ticker_specs: correct ms/sp/go for all tickers

### Fixed
- IR engine: cooldown, volume filter, min_vol check
- Executor: proper stop_loss from PG params JSONB

### Removed
- Heavy bars_list from _build_bar (was causing timeout on 120K M1 bars)

- Checkpoint: checkpoint/168-dragon-framework-integration.md

## [164] 2026-07-16
### Added
- **MT5 Continuous** — 10.5M M1 баров по 9 тикерам с 2020 (Indicative Continuous FINAM)
- **Sweep 113 continuous** — все indicative continuous символы FINAM MT5 проанализированы
- **Grid search** — sl/trail_act/trail_trail оптимизация (лучший: sl=1%, act=1.5%, trail=0.5%)
- **Time-aligned portfolio** — честный backtest без sequential бага
- **Per-ticker allocation + GO/KNUR** — alloc=12.5%/t, KNUR=0.7, GO limit 140K
- **Reinvest 3%/ticker** — риск от капитала тикера, не от всего портфеля
### Results
- **Risk 3%:** 200K→362K (+81%), MDD 4%, PF 1.76, Calmar 20.3
- **Risk 4%:** 200K→342K (+71%), MDD 6.3%, PF 1.47
- **Risk 5%:** 200K→338K (+69%), MDD 7.4%, PF 1.39
### Files
- `strategies/dragon/scripts/final_v2.py` — финальный портфель alloc+GO+KNUR
- `strategies/dragon/scripts/portfolio_alloc.py` — per-ticker alloc
- `strategies/dragon/scripts/time_aligned_portfolio.py` — time-aligned
- `strategies/dragon/scripts/sweep_113.py` — sweep всех 113 continuous
- `checkpoint/164-dragon-mt5-continuous-portfolio.md`
### Checkpoint
- checkpoint/164-dragon-mt5-continuous-portfolio.md

## [163] 2026-07-15
### Fixed
- **TZ bug в M1 backtest'ах** — часовой фильтр 07:00→15:00 IRK во всех 3 файлах (backtest.py, common/backtest.py, sweep.py)
### Added
- **Sweep M1** — `strategies/dragon/scripts/sweep_m1.py`, sweep по 8 тикерам MT5 M1
- **Портфельный бэктест** — `strategies/dragon/scripts/portfolio_test.py` с MTM DD, GO check, reinvest
### Changed
- **Dragon portfolio** — MM×2, GZ×2, GD×1 (по ГО): +38.17%, MDD 5.90%, PF 2.23, Calmar 6.5
### Checkpoint
- checkpoint/163-dragon-m1-tz-fix-portfolio.md

## [162] 2026-07-15
### Added
- **MT5 FINAM** — второй portable MT5, подключён счёт, загружено 380K M1 баров
- **Dual-write** — CH moex.mt5_bars (история) + PG futures.bars_1m (live, autopurge 2mo)
- **M1 tick** — управление позициями каждую минуту (`--mode tick`), detect остался на M5
- **Универсальный backtest** — `strategies/common/backtest.py`, читает portfolio из PG
- **Дашборд** — колонка Dragon 🐉
### Fixed
- **Data source** — `get_latest_bars()`: PG → CH mt5 → tradestats_fo → prices_5min
- **CVD** — отключён (не показал edge)
- **Dragon** — параметры оптимизированы (impulse=0.3%, retrace=70%, hump=0.1%)
### Changed
- **Cron** — mt5_bars_loader каждую минуту, tick каждую минуту
- **CHANGELOG.md** — добавлен [162]
### Checkpoint
- checkpoint/162-mt5-m1-data-pipeline.md

## [161] 2026-07-13
### Added
- **Dragon стратегия** (🐉) — адаптирована из TQA-crypto для MOEX futures
- **Sweep** по 64 тикерам — отобраны NG, MM, GZ (Score PnL/MDD)
- **Backtest** с MTM DD, реинвестом, комиссией 4₽, КНУР ×0.5
- **Paper trader** — Dragon запущен с contracts=2 на NG, MM, GZ (MTM DD ~19%)
### Fixed
- **Paper trader** — market hours gate (15:00-23:45 IRK)
- **CVD** — реальный расчёт dcvd_z из vol_b/vol_s (был хардкод 0)
- **Impulse Return** — добавлены close_hist, vol_hist (была мертва)
- **Дашборд** — MTM DD, unrealized PnL, фильтр по strategy
### Changed
- **Cron** — один portfolio вместо трёх, расписание `*/5 15-23 * * 1-5`
- **CHANGELOG.md** — добавлен [161]
### Checkpoint
- checkpoint/161-dragon-moex-strategy.md

## [160] 2026-07-09
### Added
- **MTM Drawdown** — `calc_mtm_equity()`, колонки mtm_equity/mtm_peak в PG, отображение в дашборде и run_paper_trader.py
- **Market Hours Gate** — paper trader не открывает позиции вне MOEX сессии (15:00-23:45 IRK)
- **Stale CVD Guard** — отключение CVD если tradestats_fo старше 30ч
- **Unrealized PnL per position** — дашборд показывает текущий PnL по каждой открытой позиции
### Fixed
- **CVD** — убран хардкод `dcvd_z=0`, расчёт из vol_b/vol_s (была мертва)
- **Impulse Return** — добавлены `close_hist`, `vol_hist` в bar_data (была мертва)
- **JSON serialization** — `_json_safe()` для datetime в save_state
- **Dashboard** — undefined bars_held, отсутствие mtm_equity/mtm_peak в API
### Changed
- **CHANGELOG.md** — добавлен [160]
### Checkpoint
- checkpoint/160-mtm-dd-market-hours-dashboard.md

## [156] 2026-07-08
### Fixed
- **run_paper_trader.py:** Полностью переписан — убран мёртвый импорт `PaperTrader` (класс не существует), заменён на `run_tick()` с silent-till-event паттерном. Добавлена поддержка `--strategy` и `--state-key`.
- **Cron TQA-MOEX-futures paper trader** — unpaused (был на паузе с 4 июля), расписание `*/5 0-4,11-23`
- **save_state()** — сделки теперь пишутся в `paper_trades_{state_key}`, а не хардкод в `futures.paper_trades` (4 бага: mismatched tables + dead code + missing table + orphan scripts)
- **`pt_stop_hunt.sh`** — удалён (дублировал run_moex_futures_paper.sh)
### Added
- `futures.paper_trades_stop_hunt` таблица в PG (создана)
- `~/.hermes/scripts/run_moex_futures_paper.sh` — no_agent cron wrapper
### Changed
- **AGENTS.md** — добавлена секция «🚨 Правила работы» (линтер + double-check)
- **CHANGELOG.md** — добавлен [156]
### Checkpoint
- checkpoint/156-paper-trader-recovery.md

## [154] 2026-07-07
### Added
- **CVD Momentum from DOM:** 1-min bars + CVD from order book (`moex.dom_min1`, 11.1M rows)
- **DOM data loaded:** 22 MOEX futures, 11.4B rows (2024-01 — 2026-07)
- **Bars backfilled:** `moex.bars` 2024-01-08 → 2026-06-19 (1.9M 5-min bars)
- **CVD Momentum backtester:** `strategies/cvd_momentum/backtest.py`
### Results
- Champions: **MIX** (56.5% WR, +1.9M), **TATN** (58.1%, +638K), **SNGP** (57.1%, +421K), **ROSN** (57.0%, +431K), **MTSI** (55.4%, +112K)
- Checkpoint: 154-cvd-momentum-dom-full-screen.md

## [149] 2026-07-06
### Fixed
- **REVERT: Stock futures step_price ×lot.** MOEX stock futures цены в CH — per-contract, не per-share. STEPPRICE=1.0 правильный. PG revert: GZ, RN, SR и др. step_price обратно 1.0.
- **Финальная формула:** `(exit-entry)/ms*sp*pct - TC`. Без `*lot`. Всегда.
### Changed
- **bt_5t.py:** hosts .60, CR asset CNY, PnL без `*lot` — сохранено (из 148).
- checkpoint/148 помечен как ошибочный.

## [148] 2026-07-06
### Fixed
- **⚠️ ОШИБОЧНО: Stock futures STEPPRICE per-share.** На самом деле цены per-contract. step_price=1.0 правильный. Отменено в 149.
- **PG ticker_specs:** step_price × lot_volume — отменено в 149.
- **bt_5t.py:** hosts 10.0.0.64 → 10.0.0.60; CR asset_code CNYRUBF→CNY; PnL без `*lot` — хорошие изменения, сохранены.

## [147] 2026-07-06
### Fixed
- **CRITICAL: MOEX STEPPRICE — per-contract, не per-unit.** `gross = ticks*sp*shares*pct` (без *lot). Завышало PnL для RN/GZ в 100×, для CR/Si в 1000×.
- **PG Si step_price:** 0.001 → 1.0 (MOEX standard). Был поделён на lot для компенсации бага.
- **engine.py:** `_pending` теперь list (поддерживает несколько стратегий на тикер).
### Changed
- **lib_cvd_divergence.py, mtm_portfolio.py, scan_stop_hunt.py:** убран `* lot` из PnL.
- **backtester.py:** добавлен `by_ticker` breakdown в метрики.

## [146] 2026-07-06
### Fixed
- **PnL formula — critical bug in broker.py**: `BrokerSim._close_market` was missing `*lot* pct` multipliers. `gross = ticks * step_price * shares` → `gross = ticks * step_price * shares * lot * pct`. Affected all backtests using common Engine.
- **PnL formula — mtm_portfolio.py**: `mult = lot(tkr)` → `mult = sp / ms * lot(tkr)`. Без `step_price / min_step` Si PnL был завышен в 1000× (10,000₽ вместо 10₽ за тик). Исправлено во всех 4 местах (close, floating×2, force-close).
- **PnL formula — lib_cvd_divergence.py**: `calc_pnl_rub` теперь умножает на `TICK_LOT` и `TICK_PCT` из PG.
- **scan_stop_hunt.py**: загружает `pct` из PG `futures.ticker_specs`. PnL формула: `(exit-entry)/ms*sp*lot*pct - TC`.
- **scan_stop_hunt.py**: PnL формула на линиях 70 и 86 — добавлены `* lot * pct` (было без них).
- **executor.py**: приоритет фиксированного кол-ва контрактов из `futures.portfolio.contracts` перед динамическим sizing.
- **PG portfolio**: `contracts=1` для всех enabled стратегий (было NULL — динамический sizing убивал капитал на CR).

## [143] 2026-07-05
### Changed
- Backtest with Finam reduced GO (60% of exchange margin)
- Reinvest backtest: mathematically correct, physically unrealistic
- Paper trader PnL formula fixed: no `*lot` multiplier
### Added
- Checkpoint: checkpoint/143-reduced-go-backtest.md

## [141] 2026-07-04
### Added
- Stop Hunt COMBINED (SHORT+LONG) backtest: 5 tickers, 56.4% WR, 2.03 PF, +7.3M
### Changed
- Portfolio: GD (GOLD) and RN (ROSN) added — GD 59.3% WR best performer
- LONG direction (60.5% WR) > SHORT (48.3%) — both kept for paper trader
- CR (CNYRUBF) confirmed: no data in tradestats_fo
### Fixed
- bt_5t.py: lot_volume added to spec query (was KeyError)
- bt_5t.py: CORRECT — PnL formula WITHOUT lot (step_price per contract only)
- Paper trader bugs documented (entry lag, CVD dead, timeout broken)

## [140] 2026-07-04
### Changed
- Final portfolio composition: GZ, Si, RN, GD (NG, W4, VB, SR disabled)
- Stop Hunt scan completed: 60 tickers, top by Sharpe (RN 33.8, GD 24.7)
- Answer: strategy is Stop Hunt — false breakout from MQL5 Excavator port
### Fixed
- bugs documented in paper_trader.py (entry lag, CVD dead, timeout, lot check)
### Added
- Checkpoint: checkpoint/140-stop-hunt-strategy-session.md
# Changelog

## [137] 2026-07-04
### Changed
- VB and SR removed from portfolio (negative PnL, -35K total)
- Stop Hunt partial exit tested — kills strategy (PnL goes negative)
- CVD filter tested — improves PF but cuts trade count by 43%
### Fixed
- Portfolio: SR and VB disabled in PG futures.portfolio
### Added
- Checkpoint: checkpoint/137-strategy-improvements.md

## [136] 2026-07-04
### Fixed
- CH cluster recovery: all stuck tables → ReplicatedReplacingMergeTree, 2 replicas
- Obstats backfill: 85.5M rows (more than original 46.9M) from AlgoPack API
### Changed
- Stop Hunt backtest — corrected from 81.8% WR (with look-ahead) to 51.5% (honest)
- Timeout calibration: TO=12 bars confirmed optimal (1.65 PF at 50.7% WR)
### Added
- Checkpoint: checkpoint/136-ch-cluster-recovery-backtest.md

## [192] 2026-07-29
### Added
- FINAM MT5 в Docker (контейнер mt5-finam, VNC 5901)
- Сбор стакана (DOM) через Python API (market_book_add/get)
- API сервер dom_api.py :8808 → PG
- MT5 snapshot: account, positions, deals → TimescaleDB
- Paper Trader v6 (--broker dom) с исполнением по стакану
- BrokerDOM (strategies/common/broker_dom.py)
- TimescaleDB для moex (hypertable + compression)

### Fixed
- Плодовитость terminal64 — удалён wine-runner.sh, всё через docker exec
- Убран @reboot run_finam_terminal.sh
- Checkpoint: checkpoint/192-finam-docker-dom-mt5-snapshot.md

### Fixed
- Snapshot not writing (debug print + pycache fix)
- Terminal64 proliferation in AlfaForex (wineserver -k in cron)

### Added
- docker/Dockerfile.finam — версионированный образ mt5-finam:1.0.0
- Cron для paper v6

## [193] 2026-07-30
### Added
- Portfolio backtest verified: +8,593% ROI, MTM MDD 7.92%
- 5 стратегий: IR Si, Dragon GD/MM/NG, SH RN
- Источник: FINAM MT5 (moex.mt5_continuous)
- lo_hist увеличена до 60 (SH lookback=40+)
- backtest.py переведён на mt5_continuous + params из PG

### Fixed
- Stop Hunt не работал из-за lo_hist=20 < lookback=40
- backtest.py не передавал params из PG в check_signal
- backtest.py использовал устаревший mt5_bars вместо mt5_continuous

## [204] 2026-08-02
### Added
- OI-стратегия финал: BR+NG, физ продают→long 120 мин, risk 4.5%, +5,905%, MTM DD 14.3%
- Критический аудит: look-ahead нет, slippage 2/5 живёт, ручная верификация сошлась
- scripts/oi/ — бэктесты (basic/sweep/portfolio)
- strategies/oi/paper_trader.py — live, cron */5 15-23
### Changed
- OI loader: CH 10.0.0.64→10.0.0.60, таблица futoi_iss→futoi (была readonly)
- SV отклонён (топ-3 дня=151% PnL), RN/LKOH слабеют в 2026
- Фильтр «после 19:00» убран — edge весь день
### Fixed
- OI данные не собирались с 01.07 (CH readonly из-за zookeeper)
- PnL без учёта шага цены (sp/ms)
- Checkpoint: checkpoint/204-oi-final-br-ng-5905pct.md

## [205] 2026-08-02
### Fixed
- 2 критических бага OI: (1) таймзона futoi MSK vs mt5 IRK (look-ahead 5ч), (2) guard CLOSE чужих позиций (5-мин сделки)
- Backfill: +8ч → MSK (как loader)
### Changed
- OI: BR+NG+SV, thr -3%, hold 60, risk 15% → +1,042% ROI, MTM DD 20.9%
- Свип 64 тикеров: рабочие BR/NG/SV (+RN слабее), ED/X5/SR — артефакты
### Added
- scripts/oi/backfill_futoi_iss.py (докачка ISS по датам), scripts/oi/oi_sweep_honest.py
- Данные полные до 18.07 (пробел 19-28.07 → 16.08)
- Checkpoint: checkpoint/205-oi-honest-br-ng-sv-1042pct.md

## [206] 2026-08-02
### Added
- Ролл-фильтр + обработка экспирации в OI-бэктесте (исключение сделок в дни скачков >5%)
- Результат: risk 30% → +6,300% ROI, MTM DD 14.8% (Calmar 425)
### Fixed
- Ролл-артефакты continuous: DD 22% → 7.5% (фантомные сделки через гэпы склейки)
- Проверка марта 2026: реальный тренд BR (+49%), 24% мартовского = ролл-артефакт
### Changed
- Рекомендация: risk 30% (было 15%), нужен ролл-фильтр в live
- Checkpoint: checkpoint/206-oi-roll-filter-6300pct.md

## [207] 2026-08-02
### Added
- RN в портфель OI (BR+NG+SV+RN) → +1,364% ROI, MTM DD 10.9% (risk 30%)
- Размазывание по ёмкости стакана (K=400, по DOM GAZR: стакан ~400× минутного объёма)
### Fixed
- ГО: формула КСУР-ПГО = medium(MOEX) × ставка_КСУР(XLS кол12); BR = medium (нет ПГО)
- Размазывание 25% M1 занижало ёмкость в 400-500× (по данным DOM)
### Changed
- ticker_specs: NG 3,987, SV 5,585, BR 27,657, RN 3,479
- Крон: ГО обновление 30 6 (MOEX XML + FINAM XLS)
- Checkpoint: checkpoint/207-oi-final-4-tickers-1364pct.md

## [208] 2026-08-02
### Changed
- OI risk 30% → 40% → ROI +1,199%/год (цель ≥1000%), MTM DD 14.7%
- Свип: других рабочих тикеров нет (ED — артефакт, остальные убыточны)
### Added
- Критический аудит размазывания: K=400 по DOM GAZR (25% M1 занижало в 400-500×)
- Checkpoint: checkpoint/208-oi-risk-40-1199pct-year.md

## [209] 2026-08-02
### Changed
- OI: LONG+SHORT (day_net ±3%), risk 35% → +1,094%/год, MTM DD 14.0% (честный бэктест)
- Критический аудит: фильтр свежести ≤5 мин (фантомные утренние сигналы FINAM)
- engine.py: добавлены short-сигналы (×6 ROI)
### Fixed
- TZ проверен (не кривой); AlgoPack — мусор; mt5_bars — другие цены
### Added
- Checkpoint: checkpoint/209-oi-long-short-1094pct-year.md

## [210] 2026-08-02
### Fixed
- Ролл-фильтр: топ-9 сделок бэктеста были артефактами склейки ALLFUT (BR 17.04, SV 30.01)
- is_roll_day(): expiration_date из ISS + гэп >2% + скачок >2% (было только >5% за 1 бар)
- manage_positions: roll_close — закрытие позиций в день экспирации ДО склейки
### Added
- scripts/update_expirations.py + крон 35 6 (ISS LASTTRADEDATE → PG)
- Ролл-фильтр по гэпу >2% в oi_portfolio_backtest.py
### Changed
- risk 35% → 40% → +1,309%/год, MTM DD 18.0% (с ролл-фильтром)
- Checkpoint: checkpoint/210-roll-filter-iss-expiration.md

## [211] 2026-08-02
### Added
- oi_dom: OI + подтверждение стаканом (imbalance) — DD 35%→17.5%, TATN WR 67%
- dom_pg_to_ch.py: копия PG futures.dom → CH moex.dom + autopurge 30 дней
- fetch_dom_imbalance для live
### Fixed
- Направление edge: сырьё contrarian, валюта momentum (Eu fiz momentum подтверждён)
- Бэкфилл Eu 2019-2023 (1M баров) — загружен ALLFUTEu из терминала
### Changed
- Live: RN/TATN → oi_dom (подтверждение стаканом), BR/NG/SV → oi
- Checkpoint: checkpoint/211-oi-dom-confirmation.md

## [212] 2026-08-02
### Added
- QScalp импорт: dom_qsh 1.29 млрд строк, deals_qsh 27.8M, dom_imb_qsh (ближние уровни)
- Калибровка oi_dom: TATN WR 67.4% (=PG), сырьё (BR/NG/SV) — стакан не помогает
- Портфель: oi_dom (RN/TATN) — DD 45%→21%, ROI ~тот же, Calmar ×2
### Changed
- Risk: равномерный 50% оптимум (Calmar 21); per-symbol хуже
- Checkpoint: checkpoint/212-qsh-oi-dom-calibration.md

## [213] 2026-08-02
### Changed
- Портфель: ТОП-3 (SV/RN/TATN) по PnL/ГО — BR/NG/Si отключены (маржа, эффективность)
- risk 20% (было 40%) → DD 22.8%, ROI +1,139%/7мес
### Fixed
- Занятость hold+5 (не hold+90) — ROI ×3.3 (504% → 1,686%)
- TATN не убыточен (артефакт реинвеста); Si задушен маржой на 200K
### Added
- Checkpoint: checkpoint/213-top3-margin-efficiency.md
- ЧП 214
- ЧП 215
- ЧП 216
