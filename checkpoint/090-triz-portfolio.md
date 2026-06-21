# Checkpoint 090: TRIZ Portfolio — 4-Strategy MOEX Portfolio

## Результат
**Портфель из 4 uncorrelated стратегий: +1,032% за 6.5 лет (+45.4%/год)**
**DD −31.3%, Sharpe 2.60, Calmar 1.45**

## Состав портфеля
| Стратегия | Тикер | Сигнал | Параметры | Сделок | Вес |
|-----------|-------|--------|-----------|--------|-----|
| **BR vol_LONG** | BR | Объёмный z-score > 1.3 → LONG | vol_z window=21 | 194 | 20% |
| **CR OI** | CR | OI z-score ±1.2 → mean rev | oi_z window=21 | 250 | 30% |
| **AF OI** | AF | OI z-score ±2.0 → mean rev | oi_z window=21 | 122 | 30% |
| **Si Imbalance** | Si | buy_pressure z ±1.8 → mean rev | bp_z window=21 | 135 | 20% |

## TRIZ-результаты по 5 направлениям
| Направление | Статус | Лучший сигнал |
|-------------|--------|--------------|
| Smart Money (fiz/yur) | 🟡 Слабый | fiz_buy z>2 → SHORT, yur net всегда SHORT |
| OI z-score 66 tickers | ✅ **BR/CR/AF** | BR vol_z +97%, CR oi_z +88% (gross) |
| OB Imbalance | ✅ Si buy_pressure | +39%, DD −15% |
| OI Momentum + Volume | 🟡 **BR vol LONG** | 194 сделки, +98% за 5 лет solo |
| Cross-sectional | 🟡 Нужно больше | 4 стратегии в портфеле |

## Файлы
- `strategies/moex_portfolio.py` — финальный портфельный скрипт
- `reports/triz_5direction_report.md` — полный TRIZ отчёт
- `strategies/01_fiz_yur_flow.py` — fiz/yur анализ
- `strategies/02_oi_zscore_multi.py` — OI по 66 тикерам
- `strategies/03_obstats_imbalance.py` — imbalance анализ
- `strategies/04_combined_5dir_strategy.py` — комбинированная v1
- `strategies/05_premium_5dir.py` — премиум 5-direction

## Масштабирование
| Плечо | Годовых | DD | Sharpe |
|-------|---------|-----|--------|
| 1x | +45% | −31% | 2.60 |
| 2x | +103% | −63% | 2.28 |
| 3x | +158% | −79% | 2.28 |

200%+ годовых достижимы при 3x+ плече через ГО (естественное плечо фьючерсов).
