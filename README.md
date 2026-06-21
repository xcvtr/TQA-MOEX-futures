# TQA-MOEX-futures

Торговля фьючерсами на Московской бирже (MOEX).

## Данные
- **ClickHouse** (10.0.0.63:8123, БД `moex`):
  - `prices_5m` — OHLCV фьючерсов
  - `prices_5m_oi` — OI + fiz/yur позиции
  - `tradestats_fo` — AlgoPack fo/ (6M+ строк, 2020-01-03 — 2026-06-19): secid, asset_code, OHLC, disb, vol_b/s, oi_open/close
- **Тикеры**: Si, Eu, BR, GL, ED, NG (фьючерсы с данными). Акции через asset_code не совпадают.
- **Период**: 2020-01-03 — 2026-06-19 (tradestats), 2023-01-01 — 2026-05-01 (prices_5m)

## Стратегии

### 🔴 OI Spread Divergence (LONG+SHORT) — ❌ look-ahead найден
- `checkpoint/085-ois-divergence-lookahead-audit.md`
- Показывала Calmar 68 — оказалось заглядывание в будущее
- После исправления: **только HY даёт Calmar 2.0**, остальное убыток
- `scripts/final_ls.py` (исправленная версия)

### 🟡 yur_net_z (+ OI spread) — честно, но слабо
- `checkpoint/084-yurnet-grid-search.md`
- Чистый yur_net_z + OI spread → Calmar ≤ **1.9**
- `scripts/yurnet_strategy.py` (multi-CPU, 4 TF, честный симулятор)

### ⚪ Disb-анализ (tradestats) — ❌ шум
- `checkpoint/086-disb-analysis.md`
- `scripts/analyze_disb.py` — дисбаланс агрессивных сделок (disb) как предиктор цены
- Корреляции disb→return нулевые (|r| < 0.01), WR 49-51% на всех тикерах
- GL — аномалия (Sharpe 7.6, WR 52%, но DD 100%)

## Итог по MOEX фьючерсам
Ни одна стратегия не дала убедительных результатов. Открытые направления:

## Roadmap

### 1. AlgoPack fo/ — полная загрузка и анализ (obstats, orderstats)
**Статус:** tradestats загружен (6M строк). Остальные датасеты — нет.
- **obstats** — стакан (спреды, объёмы на лучших ценах) → поиск поддержки/сопротивления
- **orderstats** — агрессивные/пассивные сделки по типам участников
- Загрузить: `moex/algopack_fo.py` (--datasets obstats, orderstats)

### 2. Кластерный анализ через стакан MOEX ISS
**Статус:** не начато
- Прямой парсинг стакана MOEX (евент-сорсинг или REST)
- Поиск кластеров ликвидности как в dom-cluster-liquidity на FOREX
- Потенциал: привязка к фьючерсам (работает на реальных данных)

### 3. Глубокий анализ OI
**Статус:** не начато
- Не OI_diff (шум на MOEX, подверждено), а накопление/распределение
- Расхождение цены и OI по сессиям (утро/день/вечер)
- fiz/yur дивергенция (yur_net_z уже частично покрывает)

### 4. Межрыночные связи
**Статус:** не начато
- Фьючерсы vs базовые активы (Si/USDRUB_FIX, BR/IMOEX)
- Поиск расхождений как сигнала для mean reversion

## Структура
- `checkpoint/` — чекпойнты (хронология, последний по номеру)
- `scripts/` — скрипты стратегий и утилит
- `config.py` — конфигурация подключения к БД
- `reports/` — результаты анализов (disb_analysis.json и т.д.)

Контекст восстанавливать из `checkpoint/`, брать последний по номеру.
