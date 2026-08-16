# 231 — TZ-стандарт: всё в МСК (биржевое время), системное решение

Дата: 2026-08-16
Статус: 🚧 РЕШЕНИЕ ПРИНЯТО: хранить ВСЁ в МСК (биржевое время MOEX). Работа идёт.
Предыдущий: checkpoint/230 (аудит: портфель аннулирован, TZ-баг dayofweek)

## 1. 🔴 Проблема (почему TZ-баг вылазит каждый раз)

| Компонент | TZ | Проблема |
|:----------|:---|:---------|
| Хост Hermes | Asia/Irkutsk +08 | fromtimestamp даёт +08 |
| CH 60 (mt5_continuous) | server Europe/Moscow | данные naive = МСК |
| CH 63 (mt5_futures) | server Etc/UTC | данные naive = МСК, но сервер трактует как UTC! |
| PG | UTC | now() = UTC |

**Корень бага**: `toUnixTimestamp(toDateTime(bt))` на CH-63 (server UTC) трактует naive
МСК-время как UTC → epoch смещён на −3ч → `datetime.fromtimestamp(ts)` на хосте +08
даёт +8ч → итог: бар 09:00 МСК виден как 17:00 локально (сдвиг +8).

**Симптом**: вход dayofweek по 17:00 вместо 09:00, prev_week_return по сдвинутым дням,
цифры ×2-4 (чекп.227 +434% → после фикса +1739%, оба под вопросом до пересчёта).

## 2. ✅ РЕШЕНИЕ (стандарт, раз и навсегда)

**ВСЕ данные хранить и читать в МСК (Europe/Moscow) как naive datetime.**
- Лоадеры ПИШУТ в CH/PG в МСК (naive, без TZ-суффикса)
- Скрипты ЧИТАЮТ как naive МСК (parse строку, НЕ fromtimestamp!)
- ЗАПРЕЩЕНО: `toUnixTimestamp + fromtimestamp` для МСК-данных
- ЗАПРЕЩЕНО: localize/astimezone/utcnow для внутренних данных
- Единственная точка конверсии: граница с внешними API (ISS=МСК, MT5=МСК)

**Правильный паттерн чтения (уже в bt_dayofweek_risk_m1.py):**
```python
SELECT toDateTime(bt) bt, ...  # naive
d = bt.date()  # naive — данные в МСК
ts = int(bt.replace(tzinfo=timezone.utc).timestamp())  # для сортировки
```

## 3. 🗺 План работ

- [ ] 1. Сканирование всех проектов: grep toUnixTimestamp/fromtimestamp/utcnow/localize
- [ ] 2. Исправить ВСЕ лоадеры (пишут МСК): mt5_moex_bridge.py (63), мост 60, futoi, ISS
- [ ] 3. Исправить ВСЕ скрипты чтения (не fromtimestamp)
- [ ] 4. Проверить PG-таблицы (paper_state, trades) — какая TZ, привести к МСК
- [ ] 5. Обновить AGENTS.md/RULES.md обоих проектов: TZ-стандарт
- [ ] 6. Создать общий хелпер tz (now_msk, parse_msk) в tqa-framework
- [ ] 7. Пересчитать dayofweek/OI на чистых данных
- [ ] 8. Решение: менять ли TZ ОС хоста на МСК (60 общий — НЕ менять! 63 смотреть)

## 4. ⚠️ Ограничения

- **Серверы 60/63: TZ ОС НЕ менять** (60 = общий хост 64, другие сервисы). Решение в коде.
- CH 63 server TZ=UTC — но данные МСК. НЕ перезапускать CH (риск), просто НЕ использовать
  toUnixTimestamp для конверсии.
- PG: конвертировать существующие записи аккуратно (бэкап перед ALTER).

## Файлы (изменены в рамках аудита, коммит 308ab06)

- scripts/bt_dayofweek_risk_m1.py — load_m1: parse naive (эталонный паттерн)
- scripts/bt_dayofweek_m1.py, bt_dayofweek_016.py, bt_dayofweek_portfolio_m1.py — то же
- strategies/dayofweek/detect.py — Вт L/Пт S + ночной фикс hour<7
- checkpoint/230 — аудит, портфель аннулирован

## Состояние для продолжения

Прод: OI + dayofweek live. Аудит выявил TZ-баг в dayofweek-скриптах (исправлен в 5).
Дальше: полное сканирование всех проектов на TZ-паттерны → фикс лоадеров/скриптов →
пересчёт. Стандарт: ВСЁ В МСК.
