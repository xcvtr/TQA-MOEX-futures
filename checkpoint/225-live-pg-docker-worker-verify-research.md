# 225 — LIVE=PG (CH изолирован), Docker-воркер, верификация исследовательских стратегий

Дата: 2026-08-14
Статус: ✅ Завершено 3 блока: (1) live на PG, (2) Docker-воркер, (3) верификация исслед. стратегий — все отброшены
Предыдущий: checkpoint/224 (OI hold 72ч + cap 2M)

## Что изменилось

### 1. 🏗 Live переведён с CH на PG (14.08)
- **Папер/detector/executor читают ТОЛЬКО PG**: bars_1m, futoi_iss, bars_d1, dom, daily_vol
- CH (10.0.0.60:8123) — ТОЛЬКО для бэктестов (mt5_continuous, futoi — вся глубина)
- Мост dual-write: M1 → CH (вся глубина) + PG bars_1m (14 дней, autopurge)
- Проверено блокировкой порта 8123: папер работает без CH (Eq 193K, сделки из PG)

| Данные | Где live | Где бэктесты | Обновление |
|:-------|:---------|:-------------|:-----------|
| M1 бары | futures.bars_1m (14д) | moex.mt5_continuous | мост dual-write |
| OI (day_net) | futures.futoi_iss (14д) | moex.futoi | loader.py (ISS) |
| Дневные close | futures.bars_d1 (120д) | — | load_bars_d1.py (cron 14:00) |
| Объёмы | futures.daily_vol | — | ISS + кэш |
| Стакан | futures.dom | moex.dom_qsh | мост |

### 2. 🐳 Docker-воркер бэктестов (общий бэктестер)
- `docker/worker/Dockerfile` + `build.sh`: tqa_framework + strategies + bt_oi_framework.py
- Читает прод-CH (read-only), пишет в тест-PG :5433 (backtest.*)
- Прогон OI 365д в контейнере: +3256%, DD 15.66%, WR 70%, PF 3.43, N=438 (идентично хосту)

### 3. 🔬 Верификация исследовательских стратегий — ВСЕ ОТБРОШЕНЫ
Методика: общий бэктестер, mt5_continuous (полная плотность), 8 тикеров, trailing TP, slippage 0/2т.
Подтверждает champion-verification (07.08): IR/SH/Dragon = фейки.

| Стратегия | N | ROI 0т | ROI 2т | WR | PF | Вердикт |
|:----------|:-:|:------:|:------:|:--:|:--:|:--------|
| Dragon | 4,284 | −39% | −62% | 45% | 0.89 | ❌ отброшена |
| Impulse Return | 7,797 | −88% | −130% | 46% | 0.89 | ❌ отброшена |
| Stop Hunt | 13,019 | −125% | −186% | 45% | 0.87 | ❌ отброшена |
| oi_dom | — | — | — | — | — | ⚠️ нет стакана (dom_qsh только TATN) |

Контроль: OI BR/NG/SV = +1729%, DD 7.8%, PF 3.85 — эталон жив.

## 🐛 Баги найдены и исправлены

1. **Backtester: позиции не закрывались** — state (bars_held/peak_fav) терялся между тиками
   (Position пересоздавался из pos_dict каждый тик) → таймаут не срабатывал, позиции висели
   до конца прогона (N=8 вместо 13K). Фикс: прокидывать state pos_dict ↔ Position.
2. **Slippage отсутствовал** в Backtester — добавлен slippage_ticks (MOEX: штраф = тики × ms).
3. **GZ/RN/MM спецификации** в bt_verify_research.py были неверны (ms=0.001 для GZ) —
   исправлены из futures.ticker_specs (GZ ms=1.0 sp=1.0, RN ms=1.0, MM ms=0.05).
4. **Скрытый баг detector**: day_net НЕ подгружался для OI (сигналы не работали бы через
   детектор) — добавлен attach_day_net (из PG futoi_iss).
5. **futoi_iss autopurge**: 2 мес → 14 дней.

## Файлы

- TQA-MOEX-futures: `strategies/common/paper_trader.py`, `engine/detector.py`, `engine/executor.py`,
  `loader.py`, `scripts/container/mt5_moex_bridge.py`, `scripts/load_bars_d1.py`, AGENTS.md
- Форк (TQA-MOEX-futures-framework): `strategies/{dragon,impulse_return,stop_hunt,oi_dom}/{detect,tick}.py`,
  `scripts/bt_verify_research.py`, `docker/worker/{Dockerfile,build.sh}`
- tqa-framework: `tqa_framework/engine/backtester.py` (state fix + slippage_ticks)

## Состояние для продолжения

Следующий шаг: **верификация прод стратегий** (OI BR/NG/SV + dayofweek SBRF/SPYF) через общий
бэктестер — те же условия что live (params из PG, slippage 1-2 тика, MTM DD). Прогон:
`scripts/bt_verify_research.py --strategy oi --days 365` (или расширить под dayofweek).
