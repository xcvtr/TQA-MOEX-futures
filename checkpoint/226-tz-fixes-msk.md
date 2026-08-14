# 226 — Все TZ-баги исправлены: данные в МСК (Europe/Moscow), рассинхрона нет

Дата: 2026-08-15
Статус: ✅ TZ-единообразие завершено, верифицировано прямыми запросами
Предыдущий: checkpoint/225 (LIVE=PG, Docker-воркер, исслед. стратегии отброшены)

## Проблема

До фикса 3 разные конвенции времени:
- Серверы 10.0.0.60/63 — Asia/Irkutsk (+8)
- CH naive — в TZ сервера (IRK)
- PG сессия — Etc/UTC
- futoi_iss в PG — БУДУЩЕЕ на 8ч (loader +5ч хак + PG UTC сессия → naive 23:45 UTC вместо 15:45)
- Скрипты с хардкодами +8/+5 (oi_watchdog), _parse_ts интерпретировал МСК-строки как IRK

## Решение: всё в МСК (Europe/Moscow)

| Компонент | Было | Стало |
|:----------|:-----|:------|
| PG timezone | Etc/UTC | Europe/Moscow (ALTER DATABASE) |
| CH timezone | IRK (системная) | Europe/Moscow (config.d/timezone.xml) |
| loader.py (ISS) | +5ч (МСК→IRK хак) | aware +03:00 |
| мост (MT5) | naive datetime | aware UTC (ts_utc) |
| папер | replace(tzinfo=utc) ×3 | astimezone(utc) |
| детектор | datetime.now() ×4 | datetime.now(timezone.utc) |
| oi_watchdog | +8/+5 хардкоды | МСК +3, toUnixTimestamp |
| futoi_iss данные | будущее на 8ч | миграция −8ч |

## Найденные рассинхроны (6)

1. **futoi_iss в PG — будущее на 8ч** → влиял на day_net в live! (loader +5ч + PG UTC)
2. **CH naive=IRK vs PG naive=UTC** — разные конвенции
3. **_parse_ts** парсил МСК-строку CH как IRK-локальную → day_net-маппинг сдвиг −5ч (N=395→521!)
4. **папер replace(tzinfo)** — перезапись тега без сдвига → age неверный
5. **cron `*/5 15-23`** IRK — НЕ покрывал вечернюю сессию (0-5 IRK = 20:00-01:00 МСК)
6. **run_moex_oi_silent.sh** — python3 (hermes venv без clickhouse-connect!) → silent collector падал

## Верификация (прямые запросы)

```
CH mt5_continuous: 19:27 МСК  unix 1786724820  ─┐
PG bars_1m:        19:27 МСК  unix 1786724820  ─┴─ ✅ совпадают
CH futoi:          19:25 МСК  unix 1786724700  ─┐
PG futoi_iss:      19:25 МСК  unix 1786724700  ─┴─ ✅ совпадают
```

- **day_net: детектор = папер** (BR 1.708=1.708, NG −1.031=−1.031, SV −0.651=−0.651) ✅
- **Бэктест OI: N=395** (вернулся после фикса _parse_ts), +1511%, DD 7.8%, WR 68.6%, PF 3.76 ✅
- **forex (FX TOP1) не затронут**: _parse_ts только для MOEX; forex — UTC-конверсия в запросе
- Контейнеры 10.0.0.60 (rann/j2t/firecrawl) в UTC — смена timezone CH их не касается
- TQA-crypto читает через toUnixTimestamp — не затронут

## Файлы

- TQA-MOEX-futures: loader.py, strategies/common/paper_trader.py, engine/detector.py,
  scripts/container/mt5_moex_bridge.py, scripts/container/mt5_bridge_tz_fix.sh,
  crontab (update_moex_oi: 15-23 → 15-23,0-5), ~/.hermes/scripts/run_moex_oi_silent.sh (venv)
- tqa-framework: tqa_framework/engine/detect.py (_parse_ts naive=МСК)
- TQA-MOEX-futures-framework: scripts/oi_watchdog.py (МСК)

## Состояние для продолжения

Следующий шаг: **верификация прод стратегий** (OI BR/NG/SV + dayofweek SBRF/SPYF) через общий
бэктестер с ПРАВИЛЬНЫМИ TZ-данными (данные теперь в МСК). Прогон: bt_verify_prod.py (OI уже
пересчитан: N=395, +1511%), bt_verify_dayofweek.py (полные D1 с 10.0.0.63 — МСК).
