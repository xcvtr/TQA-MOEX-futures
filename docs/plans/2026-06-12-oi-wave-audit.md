# План аудита: SN, AU, AL — TROUGH→LONG OI Wave Strategy

## 1. Выяснить все кандидаты
- Загрузить ВСЕ уникальные символы из `prices_5m_oi` (ClickHouse, БД moex)
- Для каждого посчитать TROUGH→LONG entry (логика дашборда: yur_net = (yur_buy - yur_sell) / total_oi * 100, lookback=12, min_change=max(2.0, std*0.5))
- Отсортировать по количеству сделок, показать полный список
- Подтвердить что SN(36), AU(6), AL(20) — действительно топ по валидным сделкам

## 2. Полный backtest по SN, AU, AL
Для каждого тикера нужно:
- Чтение данных: `prices_5m_oi` (OHLCV + OI) за 2025-01-01 … 2026-05-18
- Сигнал: TROUGH по yur_net → LONG entry
- Выход: на следующем PEAK или stop-loss 2%
- Bar-level OHLCV MTM (не exit_price!)
- Учесть комиссию Алор:
  - Для фьючерсов: 2 руб/контракт (round-trip 4 руб)
  - Для SN (Сургутнефтегаз AVDR): акции, комиссия 0.1% от суммы сделки, мин 10 руб
  - Для AU и AL: фьючерсы, 2 руб/контракт
- Размер лота:
  - SN: 1 лот = 100 акций, GO ≈ 10000 руб
  - AU: 1 контракт = 1 унция ≈ 200000 руб
  - AL: 1 контракт = 25 тонн ≈ 8000 руб
  - Капитал: 100000 RUB

## 3. Параметры тестового портфеля
Рассчитать:
- Total return %
- Max drawdown %
- Calmar ratio
- Sharpe ratio (annualized, rf=0.10 т.к. RUB)
- Win rate
- Profit factor
- Avg trade PnL (RUB)
- Trade count
- Commission impact (RUB и % от капитала)

## 4. Walk-forward
- 4 временных фолда (2025 H1, 2025 H2, 2026 Q1, 2026 Q2)
- В каждом фолде: train на предыдущих данных, test на текущем
- Показать просадку и калмар на каждом фолде

## 5. Дополнительные кандидаты (возможно, упущены)
- Проверить ticker с 1-3 сделка: BM(4), BR(1), CR, HY, LK(4), MG(8), RM(3), VB(1) — есть ли там сделки, которые можно улучшить?
- Проверить ticker с много сделок (AU 23, но только 6 TROUGH→LONG): почему остальные не прошли?

## Технические детали
- ClickHouse: host=127.0.0.1:8123, db=moex, table=prices_5m_oi
- Таблица: time, symbol, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
- Цены: moex.prices_5m (time, symbol, open, high, low, close, volume)
- JOIN: moex.prices_5m_oi.time = moex.prices_5m.time AND symbol = symbol
- Дата доступа: до 2026-05-18 (Si до 2026-05-22)
- Рабочая директория: /home/user/projects/TQA-MOEX

## Выходные файлы
- reports/oi_wave_audit/candidates_all.csv — все тикеры с кол-вом сделок
- reports/oi_wave_audit/backtest_results.json — полные результаты по SN, AU, AL
- reports/oi_wave_audit/report.md — отчёт с таблицами и выводами

ВАЖНО: 
- Никаких exit_price — только bar-level MTM по OHLCV close/open
- Не суммировать concurrent positions как отдельные портфели — один портфель, capital=100000
- Комиссии обязательны
- Если нет данных в какой-то период, честно указать
- Все цифры с комиссиями, не «предварительно» — если не проверено, не писать
