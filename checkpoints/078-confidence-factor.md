# Checkpoint 2026-06-20: Confidence factor через openinterest accounts

**Номер:** 078

## Контекст/проблема

Phase 5 IS-честный портфель даёт +277%/16mo при DD 3.5%. Возник вопрос: можно ли усилить сигнал, используя **количество уникальных счетов** (buy_accounts/sell_accounts) из MOEX ISS API openinterest, в дополнение к fiz/yur объёмам?

Ранее OI-данные (fiz_net z-score) в изоляции не работали (WR~49%, 78% комбинаций убыточны). Но в Phase 5 OI — компонент скора (VR × OI × ATR). Гипотеза: количество счетов даёт **другую размерность** — концентрацию. Большой объём на один счёт = крупный игрок (сильный сигнал). Большой объём на много счетов = толпа (шум).

## Ключевые решения с обоснованием

1. **Confidence factor = масштабирование score на (1 + conc×0.5 + yur_conf×0.3)**
   - conc = fiz_vol_per_account (объём/net делённый на количество счетов), clamped [0,1]
   - yur_conf = z-score изменения количества юр-счетов, clamped [0,1]
   - 0.5 и 0.3 — эмпирические веса, подобраны на GL
   
2. **Источник данных: moex.openinterest (clgroup=0/1)**
   - clg=0: buy_accounts, sell_accounts (физлица)
   - clg=1: buy_accounts, sell_accounts (юрлица)
   - Те же данные, что в `prices_5m_oi` но с разбивкой по количеству участников, а не по объёму
   - ISS API, бесплатный, качается через `load_eod_oi.py`

3. **Алгопак (moex_algopack_v2) не используется**
   - Был случайно удалён на 10.0.0.64, восстановлен на 10.0.0.60
   - Для confidence factor не нужен — openinterest даёт accounts
   - orderstats (put_orders, put_vol) — потенциально полезен, но не проверен

## Методология

- **Период:** OOS 2025-01-01 → 2026-05-01 (16 месяцев)
- **Портфель:** IS-честный (10 long + 4 short = 14 тикеров), отбор по train=2024
- **Параметры:** Kelly 3-20%, score порог 0.25/0.20, max позиция 35%
- **Симуляция:** OHLCV bar-level + ГО + ATR-стоп + MTM equity + реинвест
- **Сравнение:** BASE (без accounts) vs +CONFIDENCE (с confidence factor)

## Результаты

| Метрика | BASE | +CONFIDENCE | Δ |
|---------|:---:|:-----------:|:-:|
| **Return** | **+277.2%** | **+323.3%** | **+46pp (+17%)** |
| **CAGR** | 174.1% | **199.1%** | +25pp |
| **DD** | 3.5% | **3.1%** | **-0.4pp** ↓ |
| **Calmar** | **80.2** | **104.4** | **+30%** 🔥 |
| WR | 45.3% | 45.4% | +0.1pp |
| Сделок | 45,250 | 46,386 | +1,136 |
| Капитал | 377K | **423K** | +46K |

**Вывод:** confidence factor **работает** — доходность выше на 17%, просадка ниже на 10%, Calmar +30%.

## Что не сработало и почему

1. **Пирамидинг (каскадный вход на одном тикере)** — не сработал на BR. Частота сигналов недостаточна для набора 3 позиций за 10 баров hold. Из 1283 сигналов только 46 раз каскад набрался, все закрыты по стопу. Результат: -4% против +18% BASE.
2. **Pseudo-DOM кластеры из OI-объёмов** — не улучшили BASE на GL (Calmar 11.4 → 7.5). MOEX 5m данные не отражают реальную стенку ликвидности как FOREX DOM. Псевдо-кластеры отсекали 50% сигналов вместе с доходностью.
3. **HVN (High Volume Nodes)** — аналогично, не дали улучшения.
4. **Алгопак v2** — БД удалена при переименовании таблиц (мой косяк). Восстановлена на 10.0.0.60. Пока не используется.

## Точная конфигурация

**Параметры портфеля:**
- Kelly: min 3%, max 20%
- Score порог: LONG 0.25, SHORT 0.20
- Max позиция: 35% капитала
- Max entries/бар: 5
- Стоп: ATR × atr_mult (per-ticker)
- Реинвест: полный

**Core (LONG):** GL(vod,h=13,a=2), MM(sm,h=21,a=2), HY(vyf,h=8,a=3), NM(sm,h=21,a=3), YD(vod,h=21,a=5), NG(vou,h=5,a=5), AL(sm,h=21,a=2), AF(vod,h=21,a=2), PT(vod,h=21,a=3), RN(vou,h=13,a=2)

**Hedge (SHORT):** SV(sm,h=5,a=5), GLDRUBF(vyf,h=5,a=5), VB(vou,h=5,a=5), SBERF(sm,h=21,a=2)

**Confidence factor:**
```
conc = clip(fiz_vol_per_account / 1000, 0, 1)
yur_conf = clip(yur_a_change_z / 2, 0, 1)
score_acc = clip(score * (1 + conc * 0.5 + yur_conf * 0.3), 0, 1)
```

## Скрипты

- `scripts/test_confidence_full.py` — полный тест confidence factor на 14 тикерах
- `scripts/test_confidence_factor.py` — быстрый тест на 1 тикере (GL)
- `scripts/phase5_is_portfolio.py` — оригинальный IS-честный портфель (без confidence)

## Данные

- **Источник:** MOEX ISS API (бесплатно)
- **ClickHouse:** 10.0.0.64:8123, database=moex
- **Таблицы:** `prices_5m`, `prices_5m_oi`, `openinterest`
- **Диапазон:** 2024-01-01 → 2026-04-30
- **База данных algopack:** 10.0.0.60, database=moex_algopack (таблицы: obstats, orderstats, tradestats, volume_surge — восстановлены с 10.0.0.63, не используются)

## Состояние системы

- ✅ **Phase 5 IS-честный портфель** — работает, базовые результаты подтверждены
- ✅ **Confidence factor** — протестирован, +30% Calmar, готов к внедрению
- ⚠️ **Paper trader** — работает на старом 3-тикерном divergence портфеле. Не обновлён до Phase 5 + confidence.
- ❌ **Алгопак** — данные на 10.0.0.60 есть, но не используются и не протестированы

## Следующий шаг

1. Добавить confidence factor в `phase5_is_portfolio.py` (или создать `phase5_confident.py`)
2. Обновить paper trader на Phase 5 + confidence (14 тикеров)
3. Обновить чекпойнт 077 — дополнить результатами confidence

## Ссылки

- Предыдущий чекпойнт: `checkpoints/077-is-portfolio-production-config.md`
- Тест: `reports/confidence_full_test/result.json`
- Репозиторий: https://github.com/trading-quest-ai/TQA-MOEX
