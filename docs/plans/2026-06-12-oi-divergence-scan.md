# OI Divergence Analysis — Multi-TF Systematic Search

## Цель
Найти статистически значимые закономерности между OI-дивергенцией (расхождение fiz/yur) и движением цены на MOEX фьючерсах.

## Теория
- **FIZ (clgroup=0)** — розничные трейдеры, «толпа». Обычно ошибаются на разворотах.
- **YUR (clgroup=1)** — юрлица, маркет-мейкеры, «киты». Обычно действуют против толпы.
- **Divergence** — когда fiz и yur расходятся: fiz наращивает long, yur наращивает short (или наоборот).
- **Retail Trap** — fiz_max_long + yur_max_short → цена идёт против fiz.
- **Yur Divergence** — yur резко меняет позицию (buy/sell imbalance) → предвестник движения.

## Данные
ClickHouse: host=127.0.0.1, port=8123, database=moex

### openinterest (сырые данные)
- symbol, time, buy_orders, sell_orders, clgroup(0=fiz,1=yur), buy_accounts, sell_accounts
- 64 tickers, 2020-12 ~ 2026-05
- EOD-уровень (последний снапшот за день)

### prices_5m_oi (агрегированные 5m OI)
- symbol, time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
- 64 tickers, 2020-12 ~ 2026-05
- Готовая аггрегация по 5-минуткам — **основная таблица для анализа**

### prices_5m (OHLCV 5m)
- symbol, time, open, high, low, close, volume, contract
- 65 tickers, 2023-01 ~ 2026-06

### securities (справочник)
- ticker, secid, shortname, go_rub, lot, stepprice, minstep, leverage

## План

### Phase 1: Baseline scan — все тикеры, все ТФ, одна метрика
Для каждого тикера:
1. JOIN prices_5m_oi + prices_5m по (symbol, time)
2. Вычислить на каждом баре:
   - `fiz_net = fiz_buy - fiz_sell` (нетто толпы)
   - `yur_net = yur_buy - yur_sell` (нетто китов)
   - `fiz_net_z = zscore(fiz_net, window={W})` — нормированное отклонение
   - `yur_net_z = zscore(yur_net, window={W})`
   - `divergence = fiz_net_z - yur_net_z` — чем больше, тем сильнее fiz против yur
3. Сигнал: когда `|divergence| > threshold`:
   - divergence > T → SHORT (толпа в long сверх меры)
   - divergence < -T → LONG (толпа в short сверх меры)
4. Выход: через N баров или по stop-loss
5. Метрики: return%, WR, avg_win/avg_loss, Calmar (по каждому ТФ)

### TFs (таймфреймы для теста)
- Базовые окна для z-score: W = [10, 20, 40] (в 5m-барах ≈ 50мин, 1ч40м, 3ч20м)
- Hold: N = [5, 10, 20] баров
- Порог: T = [1.0, 1.5, 2.0, 2.5]

### Что выводить
Для каждого тикера × параметров:
- return%, DD%, Calmar, WR, trades_count
- Итоговая таблица: топ-10 по Calmar, топ-10 по WR

### Сохранить
- `reports/oi_divergence_scan/` — папка с результатами
- `reports/oi_divergence_scan/SUMMARY.md` — общая таблица
- `reports/oi_divergence_scan/TICKER_params.json` — per-ticker детали

## Ограничения
- NO exit_price — только OHLCV close для выхода
- NO look-ahead: z-score только на исторических данных до текущего бара
- Комиссии: 2 руб/контракт (MOEX round-trip)
- ГО умножаем на количество контрактов для расчёта полной экспозиции

## Запуск
```bash
cd /home/user/projects/TQA-MOEX
python3 scripts/oi_divergence_scan.py
```
