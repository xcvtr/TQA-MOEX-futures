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
