# 227 — dayofweek включён в live с оптимумом 2026 (+130%), MTM DD на M1, Docker-воркер пересобран

Дата: 2026-08-15
Статус: ✅ LIVE: OI + dayofweek; Docker-воркер пересобран с TZ-фиксами
Предыдущий: checkpoint/226 (TZ-фиксы, данные в МСК)

## Что изменилось

### 1. 🚀 dayofweek включён в live (SBRF/SPYF) с оптимумом 2026

Тюнинг на 2026 (общий капитал 200К, M1, MTM DD по lo/hi каждого M1-бара):

| SBRF | SPYF | ROI 2026 | MTM DD | Решение |
|:----:|:----:|:---:|:---:|:--------|
| 2x | 4x | **+130%** | **16.1%** | ✅ выбран |
| 2x | 6x | +218% | 21.3% | агрессивно |
| 4x | 6x | +232% | 25.7% | DD > лимита |

**Live-параметры (PG portfolio)**:
- SBRF: risk 10% (2x), trailing act 0.5% / trail 0.25%, SL 1.5%, timeout 24 (EOD), skip июль
- SPYF: risk 25% (4x), trailing act 0.5% / trail 0.25%, SL 1.5%, timeout 24 (EOD), skip июль

**Найден и исправлен баг**: trailing_activation/trail в PG хранились как ПРОЦЕНТЫ (0.5=50%),
папер использует ДОЛИ (0.005=0.5%). Для OI незаметно (0.99=выкл), для dayofweek ломал trailing.
numeric(5,3) округлял 0.0025→0.003 — расширен до (6,4).

**Добавлены спецификации** (ticker_specs):
- SBRF: ГО 6,050₽ (1пкт=1₽, лот 1) — 3 лота от 200К при risk 10%
- SPYF: ГО 13,461₽ (1пкт=82.6₽, ms=0.01) — 3 лота от 200К при risk 25%

### 2. ✅ dayofweek верифицирован на M1 (честный MTM DD)

| Нога | ROI (2023-26) | MTM DD | WR | Calmar |
|:-----|:---:|:---:|:--:|:--:|
| SBRF | +434% | 10.7% | 70.2% | ~40 |
| SPYF | +63% | 4.5% | 56.2% | ~14 |
| Портфель | +769% | ≤10.7% | — | CAGR 82% |

**Контроль**: без dayofweek-фильтра (все дни, та же модель) — −72.5% (DD 73%) → edge ЧИСТО dayofweek.
На M1 ROI выше чем H1 (+434% vs +267%) — trailing на минутках точнее.

### 3. 🐳 Docker-воркер пересобран с TZ-фиксами

- Образ был собран ДО TZ-фиксов (внутри не было _parse_ts МСК) — пересобран build.sh
- Прогон OI в докере после TZ: **+1511%, DD 7.76%, WR 68.6%, N=395 — идентично хосту** ✅

## Верификация (прямые запросы)

- Папер читает новые параметры: SBRF risk=0.1, SPYF risk=0.25, act=0.005, trail=0.0025, sl=0.015
- Лоты от 200К: SBRF 3, SPYF 3 (ГО 6050/13461)
- OI не тронут (act=0.99 — trailing выкл, exit по day_net)
- day_net согласован: детектор = папер (BR 1.708, NG −1.031, SV −0.651)

## Файлы

- Форк: scripts/bt_dayofweek_016.py (H1 модель 016), scripts/bt_dayofweek_m1.py (M1 + MTM DD)
- TQA-MOEX: AGENTS.md, PG portfolio (risk/trailing/SL), ticker_specs (SBRF/SPYF)
- Docker: образ tqa-moex-futures-framework-worker пересобран

## Состояние для продолжения

LIVE: OI (BR/NG/SV) + dayofweek (SBRF/SPYF risk 10/25%). Первый dayofweek-сигнал — Пн 17.08
(LONG если прошлая неделя >0). Docker-воркер актуален. Следующий шаг: наблюдение за live,
пересчёт при необходимости.
