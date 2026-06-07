# Trading Bot — Demo Trading System

**Дата:** 2026-06-08  
**Цель:** Демоторговля Volume Surge + FIZ/YUR Divergence на MOEX фьючерсах

## Архитектура

```
trading_bot/
├── config.yml          # Параметры тикеров
├── engine.py           # Ядро: z-scores, сигналы
├── scanner.py          # Сканер сигналов (загрузка данных + проверка)
├── tracker.py          # Paper трекинг позиций
├── alerts.py           # Telegram/SMS алерты
├── cron_scanner.py     # Entry point для крона
└── requirements.txt
```

## Логика сигнала

Для каждого тикера каждый 5m бар (NO LOOK-AHEAD, rolling z-score):

1. Загрузить 5m данные: time, close, open, volume, fiz_buy, fiz_sell, yur_buy, yur_sell
2. fiz_net = fiz_buy - fiz_sell; yur_net = yur_buy - yur_sell
3. zs(vals, w=20): rolling z-score от последних 20 значений
4. Сигнал: vol_z[i] ≥ vol_thresh AND |fiz_z[i]| ≥ div_thresh AND |yur_z[i]| ≥ div_thresh AND fiz_z[i] × yur_z[i] < 0
5. Направление: LONG если yur_z > 0, SHORT если yur_z < 0
6. Вход: open[i+1]
7. Выход: close[i+horizon]
8. Размер позиции: floor(капитал × риск_% / ГО) контрактов

## Тикеры для мониторинга

| Тикер | vol_z | div_z | horizon | ГО | Риск% |
|:-----:|:-----:|:-----:|:-------:|:--:|:-----:|
| HS | 2.75 | 1.5 | 12 | 5000 | 50% |
| KC | 2.0 | 2.0 | 24 | 2500 | 50% |
| DX | 3.0 | 1.5 | 48 | 3000 | 50% |
| HY | 2.5 | — | 48 | 3000 | 50% (YUR-DOM, не VS) |

## База данных

PostgreSQL на 10.0.0.64:
- moex_prices_5m_oi: symbol, time, fiz_buy, fiz_sell, yur_buy, yur_sell, total_oi
- moex_prices_5m: symbol, time, open, high, low, close, volume

DB: host=10.0.0.64, port=5432, dbname=moex, user=postgres, password=postgres
