# План: Backtest нового метода PCT_v95_yb90

## Что тестируем
Сигнал: volume ≥ 95% перцентиль AND yur_buy ≥ 90% перцентиль (rolling 20, без look-ahead)
Entry: на следующем баре после сигнала (проскок — TRIZ principle)
Exit: через N баров (40 = 3ч20м, 80 = 6ч40м) или stop-loss 2%

## Тикеры
Новый набор: BM, DX, IB, GD, CE, AF, Eu (WR > 52% по VYB h=80)
+ SN, AL, AU для сравнения со старым методом

## Что считаем
Для каждого тикера:
1. Полный backtest: capital=100K, bar-level OHLCV MTM
2. Комиссии Алор:
   - Фьючерсы (Eu, AF, CE, GD, DX, BM, IB): 2 руб/контракт round-trip
   - Узнать GO для каждого через prices_5m (средний close × размер контракта)
   - SN: акции, 0.1% от суммы
   - AL, AU: фьючерсы
3. Walk-forward 4 folds (2025 H1, 2025 H2, 2026 Q1, 2026 Q2)
4. Метрики: return%, Max DD%, Calmar, Sharpe (rf=0.10), WR, PF, trade count

## Выход
- reports/oi_volume_backtest/report.md
- reports/oi_volume_backtest/results.json

## Данные
- ClickHouse: host=127.0.0.1:8123, db=moex
- prices_5m: time, symbol, open, high, low, close, volume
- prices_5m_oi: time, symbol, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
- Рабочая директория: /home/user/projects/TQA-MOEX
