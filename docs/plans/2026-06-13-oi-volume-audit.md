# План: Анализ качества volume-confirmed OI сигналов + поиск лучшей детекции

## Этап 1. Анализ текущих сигналов
Для КАЖДОГО тикера из 64:
1. Загрузить prices_5m и prices_5m_oi
2. Вычислить rolling z-score для: volume(20), yur_buy(20), yur_net_pct(20)
3. Определить 3 типа сигналов:
   - **VYB** (Volume + Yur Buy): vol_z > 1.5 AND yb_z > 2.0
   - **VYE** (Volume + Yur Net Extreme): vol_z > 2.0 AND yn_z < -1.5
   - **YNE** (Yur Net Extreme absolute): yur_net_pct < -80
4. Для каждого сигнала: forward check на N баров (5, 10, 20, 40, 80 = 25мин, 50мин, 1ч40м, 3ч20м, 6ч40м)
5. Forward PnL: close[N] / close[0] - 1
6. WR для каждого forward horizon

## Этап 2. TRIZ-улучшение

Противоречие: 
- Хотим много сигналов (чтобы торговать), но сигналы по yur_net < -80% дают много ложных
- Хотим точных сигналов (volume spike + yur_buy spike), но их мало

ИКР: система, которая сама находит момент, когда (а) крупный игрок набирает позицию и (б) цена начинает двигаться.

Принципы TRIZ:

**Принцип динамичности** — не фиксированный порог, а:
- percentile от истории (топ-5% volume + топ-10% yur_buy)
- адаптивный rolling window (не 20, а смотря по волатильности)

**Принцип матрёшки** — stacked confirmation:
1. Volume > 90% percentile + yur_buy > 80% percentile
2. Цена закрытия выше volume-weighted среднего за 20 бар
3. yur_net_pct не в нейтральной зоне (не от -20 до +20)

**Принцип проскока** — входить не на баре аномалии, а:
- на следующем баре (после того, как аномалия подтверждена)
- или на первом откате после аномалии

**Принцип обратной связи**:
- Сигнал плохой (WR < 40%) → ужесточить пороги
- Сигнал хороший (WR > 60%) → ослабить пороги
- walk-forward 4 folds

## Этап 3. Полный перебор

Для всех 64 тикеров:
1. Для каждого типа сигнала — forward test на 4 horizon (5, 20, 80 баров)
2. Худшие тикеры отсечь (WR < 45% или avg return < 0)
3. Лучшие проходят walk-forward

## Этап 4. Лучшая комбинация

Искать лучший набор параметров:
- volume_threshold: [50, 70, 80, 90, 95] percentile
- yur_buy_threshold: [50, 70, 80, 90, 95]
- forward_horizon: [5, 10, 20, 40, 80]
- exit: на следующем экстремуме или по SL/TP

## Технические детали
- ClickHouse: host=127.0.0.1:8123, db=moex
- prices_5m: time, symbol, open, high, low, close, volume
- prices_5m_oi: time, symbol, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
- JOIN: symbol + time
- Все rolling z-scores — ТОЛЬКО по истории до текущего бара (никакого look-ahead)
- Рабочая директория: /home/user/projects/TQA-MOEX
- Выход: reports/oi_volume_audit/

## Выходные файлы
- reports/oi_volume_audit/signal_quality.md — WR по каждому тикеру×тип сигнала×горизонт
- reports/oi_volume_audit/best_params.json — найденные лучшие параметры
