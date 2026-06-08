# Order Block Redesign — Cross-Validation Test

## Цель
Протестировать 3 варианта Order Block стратегии на всех тикерах MOEX, 4 таймфреймах, 4 горизонтах выхода. Выявить лучшую комбинацию.

## Базовые параметры (единые для всех вариантов)
- body_mul = 1.5 (порог тела displacement)
- range_mul = 1.2 (порог range displacement)
- lookback = 20 (окно rolling median)

## Таймфреймы тестирования
['5m', '15m', '30m', 'H1']
Ресемпл из 5m данных: последняя свеча (close), max(high), min(low), first(open), sum(volume).

## Горизонты выхода (в свечах соответствующего ТФ)
{'5m': [3, 4, 6, 8], '15m': [2, 3, 4, 6], '30m': [2, 3, 4], 'H1': [2, 3, 4]}

## Тикеры (64 шт, все из moex_prices_5m кроме TEST_TICKER)
```python
TICKERS = ['AF','AL','AU','BM','BR','CC','CE','CH','CNYRUBF','CR','DX','ED','Eu','EURRUBF','FF','GAZPF','GD','GK','GL','GLDRUBF','GZ','HS','HY','IB','IMOEXF','KC','LK','MC','ME','MG','MM','MN','MX','MY','NA','NG','NM','NR','OJ','PD','PT','RB','RI','RL','RM','RN','SBERF','SE','SF','Si','SN','SP','SR','SS','SV','TN','TT','UC','USDRUBF','VB','VI','W4','X5','YD']
```

## Варианты реализации

### Variant A — Displacement Breakout (fix текущей)
- Детектим displacement: body > 1.5× median_body AND range > 1.2× median_range
- OB = свеча ПЕРЕД displacement (индекс displacement-1)
- Entry = OPEN displacement-бара (breakout entry)
- Exit = CLOSE через horizon баров
- Direction: 
  - closes[i] > opens[i] → LONG (бычий импульс)
  - closes[i] < opens[i] → SHORT (медвежий импульс)
- Return: LONG = (exit - entry)/entry*100, SHORT = (entry - exit)/entry*100

### Variant B — True ICT Order Block with Retest
- Детектим displacement и OB (как в A)
- Entry = ПОСЛЕ retest: ищем момент, когда цена возвращается к OB-level
  - LONG: цена касается или пересекает OB level (low[ob_idx]) в пределах tolerance 0.1%
  - SHORT: цена касается или пересекает OB level (high[ob_idx]) в пределах tolerance 0.1%
  - Retest должен произойти в пределах max_retest_bars = 30 от displacement
- Entry = цена retest-бара (close retest бара)
- Exit = CLOSE через horizon баров ПОСЛЕ retest
- Если retest не найден в max_retest_bars — сигнал пропускается

### Variant C — Displacement + OB Level Entry (без ожидания retest)
- Детектим displacement и OB
- Entry = OB level (low[ob_idx] для LONG, high[ob_idx] для SHORT), а не open displacement
- Exit = CLOSE через horizon баров
- Если entry level не достигнут к моменту close[displacement+horizon] — exit всё равно по close

## Алгоритм работы скрипта

1. Подключиться к БД (host=10.0.0.64, port=5432, dbname=moex, user=postgres, password=postgres)
2. Для КАЖДОГО тикера:
   a. Загрузить 5m OHLCV: SELECT time, open, high, low, close, volume FROM moex_prices_5m WHERE symbol=%s ORDER BY time
   b. Если < 100 баров — SKIP (недостаточно истории)
   c. Для КАЖДОГО ТФ (5m, 15m, 30m, H1):
      - Ресемплировать данные (агрегация: OHLC + volume sum)
      - Для КАЖДОГО варианта (A, B, C):
        - Для КАЖДОГО horizon:
          - Запустить detect_signals
          - Собрать статистику: n сигналов, WR, PF, avg_return, max_dd
   d. Сохранить результаты в CSV

## Формат вывода

### 1. leaderboard.csv — сводная таблица (TOP-10 по WR на каждую комбинацию)
ticker,tf,variant,horizon,n,wr,pf,avg_return,max_dd

### 2. by_variant_<A/B/C>.csv — лучшие 20 комбинаций для каждого варианта
### 3. best_per_ticker.csv — лучшая комбинация для каждого тикера
### 4. best_per_tf.csv — лучшая комбинация для каждого ТФ

## Констрейнты (HARD)

1. NO LOOK-AHEAD BIAS — rolling median ТОЛЬКО по предыдущим барам
2. direction-specific return: LONG=(exit-entry)/entry, SHORT=(entry-exit)/entry
3. В Variant B retest_bar_time > displacement_bar_time (нельзя войти до импульса)
4. Пропускать тикеры с < 50 сигналами в статистике (недостаточно данных)
5. PF cap at 999.99 (защита от float('inf'))
6. Skip тикеры < 100 баров данных
7. Все расчёты в памяти — один проход на тикер, без сохранения промежуточных файлов
8. Output CSV сохранить в /home/user/projects/TQA-MOEX/docs/plans/ob_results/

## Ожидаемое время выполнения
~5-15 минут (62 тикера × до 4 TF × загрузка данных)

## Проверка результата
- Файлы .csv созданы в ob_results/
- Есть хотя бы один TOP-10 leaderboard с WR > 55%
- n сигналов > 0 хотя бы для 20+ тикеров
