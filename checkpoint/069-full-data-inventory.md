# Checkpoint 2026-06-19: Полный инвентарь MOEX данных + orderstats загружен

## Контекст/проблема
Необходимо было выжать всё из подписки MOEX AlgoPack v2 для торговли против толпы. Ранее были загружены `tradestats` и `obstats` (акции). Обнаружили, что по тому же токену доступны: `orderstats` (eq), а также полный набор для **FX** (валютный рынок MOEX): tradestats, obstats (c L2-стаканом!), orderstats. Срочный рынок (futures/derivatives) недоступен — нужна отдельная подписка.

Дополнительно: вычищены гигантские JSON (~2 ГБ) из истории git, которые блокировали push.

## Ключевые решения

### 1. orderstats (eq) — загружен
- **Что это**: лимитные заявки до сделок (put/cancel orders), разделение buy/sell, VWAP заявок
- **Почему**: опережающий индикатор по отношению к tradestats. Заявки показывают намерения до исполнения
- **Сигналы**: put/cancel ratio, VWAP лимитников как уровни S/R, аномалия cancel >> put = разворот

### 2. FX наборы — доступны, не загружены
- tradestats, obstats, orderstats для 15 валютных пар (USD, CNY, GLD, BYN, KZT, AMD, TRY и др.)
- **obstats (fx) содержит L2-стакан** глубиной L1-L20 с micro_price и VWAP по уровням — аналог DOM для валюты
- Решение: не загружать сейчас, сосредоточиться на анализе orderstats (eq)

### 3. Вычищена история git
- 22 файла `reports/oi_divergence_scan/*_params.json` по 60-100 MB каждый (~2 ГБ в истории)
- `git filter-repo` + force push
- Добавлены в `.gitignore`

### 4. ClickHouse кластер
- 10.0.0.63: все AlgoPack v2 таблицы (tradestats, obstats, orderstats) + Distributed
- 10.0.0.60: futures_5m + OI данные
- VIP 10.0.0.64: единая точка входа
- MergeTree (не Replicated) — Keeper нестабилен

## Полный инвентарь данных

### Фьючерсы (срочный рынок) — 5m бары

| Таблица | Период | Строк | Инструментов | Ключевые колонки |
|---------|--------|-------|-------------|------------------|
| `moex.prices_5m` | 2023-01-03 → 2026-06-09 | 5,877,679 | 65 | OHLCV |
| `moex.prices_5m_oi` | 2020-12-25 → 2026-05-22 | 9,599,758 | 64 | **yur_buy, yur_sell, fiz_buy, fiz_sell, total_oi** |

### Акции (AlgoPack v2 eq) — 1m бары

| Таблица | Период | Строк | Инструментов | Ключевые колонки |
|---------|--------|-------|-------------|------------------|
| `moex_algopack_v2.tradestats` | 2020-01-03 → 2026-06-18 | 1,720,000 | 314 | OHLCV + buy/sell split + disb |
| `moex_algopack_v2.obstats` | 2020-01-03 → 2026-06-18 | 1,720,000 | 329 | Спреды BBO/LV10/1M, imbalance |
| **`moex_algopack_v2.orderstats`** 🆕 | **2020-01-03 → 2026-06-18** | **1,721,000** | **330** | **put/cancel orders** |

### Валюта (AlgoPack v2 fx) — доступно, не загружено

| Набор | Инструментов | Особенность |
|-------|-------------|-------------|
| tradestats (fx) | 13 | OHLCV + buy/sell split |
| obstats (fx) | 15 | **L2-стакан L1-L20**, micro_price |
| orderstats (fx) | 15 | put/cancel заявки |

## Что orderstats даёт для торговли против толпы

Колонки:
- `put_orders_b/s` — количество поставленных лимитных заявок (B=bid, S=ask)
- `cancel_orders_b/s` — количество **снятых** заявок
- `put_val_b/s`, `put_vol_b/s` — объём заявок в RUB/штуках
- `put_vwap_b/s` — средневзвешенная цена заявок

**Сигналы:**
1. **Put/Cancel Ratio** — когда `cancel >> put`, толпа снимает заявки → готовится пробой
2. **VWAP лимитников** — естественные уровни S/R (куда толпа метит)
3. **orderstats vs tradestats divergence** — намерения расходятся с исполнением
4. **Аномалии cancel без перевыставления** → ложный пробой

## Чего не хватает (платные источники)

| Что | Источник | Цена (ориентир) |
|-----|----------|-----------------|
| Юр/физ split на акциях | MOEX FTP обезличенных сделок | ~50K ₽/мес |
| Фьючерсный L2-стакан | MOEX Info Board / L2 feed | ~30-50K ₽/мес |
| Чужие позиции (1D) | Дорогой AlgoPack tier | ~100K+ ₽/мес |

## Git-инфраструктура

- Remote: `origin https://github.com/xcvtr/TQA-MOEX.git`
- `.gitignore` обновлён: добавлены `*_params.json`
- История переписана (git filter-repo) — удалены ~2 ГБ JSON из всех коммитов
- Force push выполнен

## Конфигурация

### ClickHouse
- Кластер: `forex_cluster` (1 шард, 2 реплики: 10.0.0.63:9000 + 10.0.0.60:9000)
- VIP: 10.0.0.64:9000 (единая точка входа)
- Движок AlgoPack таблиц: MergeTree (не Replicated — Keeper нестабилен)
- Партиции: `toYYYYMM(tradedate)`
- Порядок сортировки: `(secid/ticker, tradedate, tradetime)`

### Скрипты
- `scripts/algopack_load_v2.py` — загрузка tradestats/obstats
- `scripts/orderstats_load.py` — загрузка orderstats 🆕
- `config.py` — CH_HOST=10.0.0.64
- `loader.py` — CH_HOST=10.0.0.64

### Токен
- В `.env`: `ALGOPACK_APIKEY=eyJ...`
- Для скриптов: `T=$(grep ALGOPACK_APIKEY .env | cut -d= -f2-)`

## Состояние системы

| Компонент | Статус |
|-----------|--------|
| ClickHouse 10.0.0.63 | ✅ Нода активна |
| ClickHouse 10.0.0.60 | ✅ Нода активна |
| VIP 10.0.0.64 | ✅ Ведёт на 10.0.0.60 |
| tradestats | ✅ 1.72M rows |
| obstats | ✅ 1.72M rows |
| orderstats | ✅ 1.72M rows |
| futures_5m | ✅ 5.88M rows (на 10.0.0.60) |
| futures_5m_oi | ✅ 9.60M rows (на 10.0.0.60) |
| FX datasets | 📥 Доступны, не загружены |

## Следующий шаг
1. **Анализ orderstats**: put/cancel ratio heatmap, корреляция с tradestats, VWAP уровни
2. **Daily cron** для догрузки: tradestats, obstats, orderstats ежедневно после закрытия
3. По желанию: загрузить FX наборы (валютный рынок MOEX с L2-стаканом)

## Roadmap
Сохранён в Obsidian: `Trading/2026-06-19_2155_moex_algopack_roadmap.md`

## Ссылки
- Предыдущий: `068-algopack-v2-cluster.md`
- Roadmap: `2026-06-19_2155_moex_algopack_roadmap.md` (Obsidian)
- Проект: `~/projects/TQA-MOEX/`
- Репозиторий: `origin https://github.com/xcvtr/TQA-MOEX.git`
- ClickHouse: 10.0.0.64:9000 (VIP), 10.0.0.63:9000, 10.0.0.60:9000
