# TRIZ 300% Phase 4 — Полный метагрид: per-ticker + chandelier + stacked

## Задача

Провести полный перебор для каждого тикера всех комбинаций параметров, найти комбинации дающие >300% годовых.

## Данные

ClickHouse 127.0.0.1:8123, БД moex. Таблицы: prices_5m (time,symbol,open,high,low,close,volume), prices_5m_oi (time,symbol,fiz_buy,fiz_sell,yur_buy,yur_sell,total_oi). Данные до мая 2026.

Тикеры с OI: RI, GL, USDRUBF, AF, BR, IMOEXF, CC, NM, PD, SV, VB, GD, SR, LK, PT, Si, Eu, CNYRUBF, CR, NG, MX, AL, RN

GO: RI=27034, GL=1352, USDRUBF=11186, AF=673, BR=17228, IMOEXF=2596, CC=506, NM=256, PD=24487, SV=12960, VB=1556, GD=32003, SR=6620, LK=11606, PT=31749, Si=12330, Eu=14478, CNYRUBF=875, CR=17200, NG=8027, MX=4133, AL=728, RN=3152

CS (contract size): RI=1, GL=1, USDRUBF=1000, AF=1, BR=10, IMOEXF=10, CC=10, NM=10, PD=1, SV=10, VB=100, GD=1, SR=100, LK=10, PT=1, Si=1000, Eu=1000, CNYRUBF=1000, CR=10, NG=100, MX=1, AL=100, RN=100

## Паттерны (все 5)

1. vol_up_oi_up_yb_up: dv>0 AND dtoi>0 AND dyb>0
2. smart_money: dv>0 AND dyb>0 AND dfn<0
3. vol_up_oi_down: dv>0 AND dtoi<0
4. vol_up_yb_down_fiz_up: dv>0 AND dyb<0 AND dfn>0
5. fiz_extreme_vol_up: dv>0 AND abs(dfn)>5

## Параметры для grid search per ticker

hold: [1, 2, 3, 5, 8, 13, 21]
sl_pct: [0.005, 0.01, 0.02]
dv_threshold: [0, 1.0, 2.0]

### Chandelier exit
atr_mult: [2.0, 3.0, 5.0]  (trailing stop = ATR * atr_mult от peak)
min_stop: 0.01
max_loss: 0.05
use_chandelier: [True, False] (комбинация: если use_chandelier=True, sl_pct игнорируется)

### Partial exit (если use_chandelier=True)
use_partial_exit: [True, False]
partial_atr_mult: [0.5, 1.0]  (закрыть 50% на ATR*partial_atr_mult)

### Stacked confirmation (relaxed)
use_stacked: [True, False]
stacked_fiz_thr: [0.5, 1.0]  (fiz_net z-score > порога)
stacked_vol_thr: [1.0, 1.5]  (volume z-score > порога)
Смысл: если daily паттерн + stacked_fiz_z > порога OR stacked_vol_z > порога — вход

### CBR filter
use_cbr: True (всегда)

### Score sizing
use_score_sizing: [True, False]  (капитал по Calmar)

### Капитал
100_000 и 200_000

## Результаты

Для каждого тикера:
- Все комбинации параметров
- Для каждой: ret, dd, calmar, wr, pf, trades, ann
- Только combo с ret > 0 и trades >= 8

Итоговый портфель:
- Топ-10 комбинаций по Calmar (непересекающиеся по ticker)
- Симуляция портфеля с MTM

Выход:
- reports/triz_phase4/per_ticker_grid_{ticker}.json
- reports/triz_phase4/per_ticker_best.json
- reports/triz_phase4/portfolio_result.json
- reports/triz_phase4/report.md

## Ссылки на код

- /home/user/projects/TQA-MOEX/reports/triz_diamond_v2/diamond_search.py — как загружать данные, ATR, бэктест
- /home/user/projects/TQA-MOEX/reports/triz_diamond_v4/diamond_search_v4.py — chandelier exit + partial exit
- /home/user/projects/TQA-MOEX/reports/triz_diamond_v4/portfolio_v4.py — портфельная симуляция

Венв: /home/user/venvs/tqa/main/bin/python3
ClickHouse даты через пробел, не T.

Напиши полный скрипт, запусти, результаты в файлы. Делай самостоятельно.
