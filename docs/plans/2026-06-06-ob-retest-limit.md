# Order Block — Limit Order Retest

## Цель
Перетестировать OB стратегию (Variant A — Displacement Breakout) с реалистичным входом через лимитные ордера вместо рыночного.

## Тикеры (58 шт, все с WR ≥ 70% на Variant A H1)
AF, AL, BM, BR, CC, CE, CR, DX, ED, FF, GAZPF, GD, GK, GL, GZ, HS, HY, IB, IMOEXF, KC, LK, MC, ME, MG, MM, MN, MX, MY, NA, NG, NM, NR, OJ, PD, PT, RB, RI, RL, RM, RN, SBERF, SE, SF, Si, SN, SP, SR, SS, SV, TN, TT, UC, VB, VI, W4, X5, YD
(исключены валютные: Eu, CNYRUBF, EURRUBF, USDRUBF, GLDRUBF — 54 осталось)

## Параметры
- body_mul=1.5, range_mul=1.2, lookback=20
- Только H1 (лучший ТФ)
- Горизонты: [2, 3, 4]
- TF: H1 (ресемпл из 5m)

## Логика лимитного ордера (Variant D — Limit at OB Level)

1. Детектим displacement (как в Variant A):
   - body[i] > med_body[i] * 1.5 AND range[i] > med_range[i] * 1.2
   - direction = LONG если c[i] > o[i], иначе SHORT

2. Order Block = свеча ПЕРЕД displacement (idx-1)

3. Лимитный вход:
   - LONG: LIMIT BUY на low[ob_idx] (цена OB-уровня)
   - SHORT: LIMIT SELL на high[ob_idx]
   - Ждём до limit_lookback = 5 баров H1 (5 часов) для исполнения
   - Ищем fill_bar: для LONG — bar j где low[j] <= level, для SHORT — high[j] >= level
   - Если заполнился: entry = level, exit = close[fill_bar + horizon]
   - Если не заполнился: сигнал пропускаем

## Variant E — Limit at Displacement Close (для сравнения)

1. Детектим displacement (как выше)

2. Лимитный вход на close[i]:
   - LONG: LIMIT BUY на close[i]
   - SHORT: LIMIT SELL на close[i]
   - Ждём до limit_lookback=5 для исполнения
   - Ищем fill_bar: для LONG — close[j] >= level, для SHORT — close[j] <= level
   - Если заполнился: entry = level, exit = close[fill_bar + horizon]

## Output CSV
- leaderboard_limit.csv: колонки ticker, variant (D/E), horizon, n, wr, pf, avg_return, max_dd, fill_rate
- by_variant_D.csv, by_variant_E.csv — TOP-20 каждого
- best_per_ticker_limit.csv — лучшая комбинация на тикер

## Констрейнты
- NO LOOK-AHEAD
- direction-specific return: LONG=(exit-entry)/entry, SHORT=(entry-exit)/entry
- PF cap at 999.99
- Min 50 signals для статистики
- Скрипт: /home/user/projects/TQA-MOEX/scripts/ob_limit_test.py
- Вывод: /home/user/projects/TQA-MOEX/docs/plans/ob_results/
