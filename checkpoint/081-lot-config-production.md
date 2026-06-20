# 035 — Lot Size Config + Reduced for HY/AF (2026-06-15)

## Контекст
Увеличили размер лота с 50% до 100% капитала на контракт. 
HY (DD=19.6%) и AF (DD=23.9%) выбивались — для них оставили 75%.

## Финальный production config
```python
BASE_V2_SCORE_THRESH = 0.10
BASE_V2_BARS_LEFT = 8
BASE_V2_STOP_ATR = 1.0
BASE_V2_LOT_PCT = 1.00
BASE_V2_LOT_PCT_REDUCED = 0.75
BASE_V2_REDUCED_TICKERS = ['HY', 'AF']
```

## Состояние системы
✅ BASE v2: score>0.10, bars=8, stop=1.0A
✅ Размер лота: 100% (цель DD 12-18%)
✅ HY/AF: 75% лота
✅ OOS-валидация пройдена
✅ config.py — production config

## Активные кроны (ключевые)
— cron daily MOEX options scan (04:00 MSK)
— Нет крон под фьючерсную стратегию (пока ручной режим)

## Что делать дальше (приоритет)
1. Исследовать SHORT-сигналы (текущая стратегия только LONG)
2. Алготрейдинг deployment
3. Опционное хеджирование
