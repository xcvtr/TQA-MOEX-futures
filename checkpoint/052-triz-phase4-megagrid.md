# Checkpoint 052: TRIZ Phase 4 — Megagrid завершён

## Результаты

**16,950 комбинаций** за 213 секунд. 23 тикера × 7 hold × chandelier × stacked × partial.

## Топ-50 алмазов (по Calmar)

| # | Ticker | Pattern | H | Ch | Ret | DD | Calmar | WR |
|---|--------|---------|---|----|-----|-----|--------|-----|
| 1 | **GL** | vol_up_oi_up_yb_up | 21 | Y | **+755.5%** | 11.9% | **63.8** | 67% |
| 2 | **AF** | smart_money | 8 | Y | +66.4% | 1.4% | **48.0** | 90% |
| 3 | **AF** | vol_up_oi_up_yb_up | 8 | Y | +54.0% | 0.3% | **194.6** | 92% |
| 4 | **AF** | vol_up_oi_up_yb_up | 21 | Y | +67.7% | 0.3% | **235.3** | 93% |
| 5 | **IMOEXF** | vol_up_oi_up_yb_up | 8 | Y | +45.4% | 0.2% | **291.5** | 91% |
| 6 | **IMOEXF** | vol_up_oi_up_yb_up | 21 | Y | +108.2% | 0.9% | **125.4** | 85% |
| 7 | **CC** | chandelier+stacked | 5 | Y | +73.4% | 0.6% | **132.4** | 67% |
| 8 | **CC** | vol_up_oi_up_yb_up | 5 | Y | +50.6% | 0.4% | **121.5** | 67% |
| 9 | **GD** | vol_up_oi_down | 1 | N | +22.8% | 0.2% | **103.3** | 75% |
| 10 | **CNYRUBF** | vol_up_oi_down | 21 | Y | +27.9% | 0.3% | **94.4** | 86% |

## Ключевые открытия

1. **Chandelier exit с long hold (8-21)** — game changer
   - GL hold=21 chandelier: **+755%**, DD 11.9%, Calmar 63.8!
   - AF hold=21 chandelier: +67.7%, Calmar 235.3
   - IMOEXF hold=8 chandelier: +45.4%, Calmar 291.5

2. **AF с chandelier — best risk-adjusted**
   - AF smart_money hold=8 chandelier: +66.4%, DD 1.4%, Calmar 48.0
   - AF vol_up_oi_up_yb_up hold=8: +54%, DD 0.3%, Calmar 194.6

3. **RI и USDRUBF** — 0 profitable combos (требуют большего капитала или других параметров)

4. **Stacked confirmation** не даёт большого прироста поверх chandelier

## Новые файлы

- `reports/triz_phase4/megagrid.py` — компактный grid search
- `reports/triz_phase4/grid_{ticker}.json` — per-ticker результаты (17 файлов)
- `reports/triz_phase4/all_best.json` — топ-50 комбо

## Что дальше

1. **Портфельная симуляция** — не estimated, а MTM по всем сигналам
2. **Per-ticker capital allocation** — разный капитал на каждый тикер
3. **Аудит GL +755%** — не переобучение ли это (3 сделки с WR 67% могут дать +755% из-за плеча)

**Лучший результат: GL hold=21 chandelier → +755.5% за 2 года, Calmar 63.8**
