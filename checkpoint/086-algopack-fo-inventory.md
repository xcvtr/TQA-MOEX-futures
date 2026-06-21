# 086 — MOEX AlgoPack fo: полная инвентаризация данных (2026-06-21)

## Что мы использовали раньше
Все стратегии строились на `moex.prices_5m` + `moex.prices_5m_oi` — это **старые OI данные** (yur_buy, yur_sell, fiz_buy, fiz_sell, total_oi), которые:
- Обновляются раз в 5 минут (позиции, а не сделки)
- Не содержат дисбаланса агрессивных сделок
- Дают Calmar ≤ 1.9

## Что есть в AlgoPack fo (фьючерсы+опционы)
Доступно по токену `ALGOPACK_APIKEY` через `https://apim.moex.com/iss/datashop/algopack/fo/`

### 1. ✅ tradestats — **1-минутные бары** (КЛЮЧЕВОЙ НАБОР)
**33 колонки:**
| Колонка | Тип | Описание |
|---------|-----|----------|
| **disb** | double | **Дисбаланс агрессивных сделок** (vol_b - vol_s)/(vol_b + vol_s). -1..+1. Аналог «лямов»! |
| vol_b / vol_s | int64 | Объём покупок / продаж в контрактах |
| val_b / val_s | double | Объём покупок / продаж в рублях |
| trades_b / trades_s | int32 | Количество сделок на покупку / продажу |
| pr_open/high/low/close | double | OHLC |
| pr_vwap / pr_vwap_b / pr_vwap_s | double | VWAP общий / покупок / продаж |
| **oi_open/high/low/close** | int64 | **Open Interest: OHLC!** Не snapshot, а полноценные свечи по OI |
| vol | int64 | Объём в контрактах |
| val | int64 | Объём в рублях |
| trades | int32 | Количество сделок |
| pr_std | double | Стандартное отклонение цены |
| pr_change | double | Изменение цены |
| im | double | Initial Margin (ГО) |
| sec_pr_open/high/low/close | int32 | Цены базового актива в пунктах |
| asset_code | string | Базовый актив (Si, GL, HS и т.д.) |
| secid | string | Код инструмента (SiU5, GLU5) |
| SYSTIME | datetime | Время загрузки данных |

### 2. ✅ obstats — **Статистика стакана**
Спреды BBO, LV, дисбаланс стакана, микроцены. Пока не анализировали.

### 3. ✅ orderstats — **Статистика лимитных заявок**
Put/cancel orders, VWAP заявок. Опережающий индикатор по отношению к сделкам.

### 4. ❌ trades — **404**. Не входит в подписку.
### 5. ❌ orderbook — **404**. Полный стакан L1-L20+ не доступен.
### 6. ❌ candles — **404**. Готовые свечи не доступны.

## Разница между старыми данными и AlgoPack fo

| Аспект | Старые данные (prices_5m_oi) | AlgoPack fo tradestats |
|--------|------------------------------|------------------------|
| Частота | 5 минут | **1 минута** |
| Дисбаланс | yur_net (позиции, lagging) | **disb** (поток сделок, real-time) |
| OI | total_oi (один snapshot) | **oi_open/high/low/close** (OHLC свечи!) |
| Buy/Sell объём | нет | **vol_b / vol_s** |
| VWAP | нет | **pr_vwap / pr_vwap_b / pr_vwap_s** |
| Период | 2023-2026 | 2020-2026 (глубже) |

## Статус загрузки в ClickHouse
**Пока НЕ загружено!** Наш ClickHouse содержит:
- `moex_algopack_v2.tradestats` — это **акции (eq)**, **НЕ фьючерсы**
- `moex_algopack_v2.obstats` — акции
- `moex_algopack_v2.orderstats` — акции

Фьючерсные `fo/` датасеты **надо загрузить отдельно**.

## Что даёт disb
disb = (vol_b - vol_s) / (vol_b + vol_s)

- **disb > 0.5** → агрессивные покупатели доминируют → вероятен рост
- **disb < -0.5** → агрессивные продавцы доминируют → вероятно падение
- **disb ≈ 0** → равновесие

Это именно то, что мы искали как «лямы» — реальный поток крупных сделок, а не запаздывающие OI позиции.

## План действий
1. **Загрузить** `fo/tradestats` для всех 6 тикеров в ClickHouse
2. **Проверить** корреляцию disb → next_bar_return
3. **Построить** стратегию на disb без заглядывания в будущее
4. Повторить для `fo/orderstats` и `fo/obstats`

## Ссылки
- Предыдущий: `085-ois-divergence-lookahead-audit.md`
- API: `https://apim.moex.com/iss/datashop/algopack/fo/`
- Токен: в `.env` проекта
