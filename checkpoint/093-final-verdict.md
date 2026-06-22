---
title: "TRIZ Portfolio v3 — 3 новых направления исследованы"
checkpoint: 093
date: 2026-06-22
tags: [checkpoint, TQA-MOEX-futures, portfolio, triz, done]
---

┌──────────────────────────────────────────────────────────────┐
│ Checkpoint 093: TRIZ 3 направления — финальный вердикт      │
├──────────────────────────────────────────────────────────────┤
│ Date:   2026-06-22                                          │
│ Period: 2020-01-03 → 2026-06-19 (6.5 yr)                   │
│ Capital: 100,000 RUB                                        │
│ Project: TQA-MOEX-futures                                   │
└──────────────────────────────────────────────────────────────┘

## Итоговая таблица всех TRIZ-направлений

┌──────────────────────────────────────────────────────────────────────────────┐
│ Направление               | Данные       | Результат | Статус               │
├──────────────────────────────────────────────────────────────────────────────┤
│ 1. OI z-score (AF th=2.0) | supercandles | +89%/5yr  │ ✅ работает          │
│ 2. Volume Imbalance (Si)  | obstats_fo   | +39%/5yr  │ ✅ работает          │
│ 3. Smart Money (fiz/yur)  | prices_5m_oi | слабый    │ ❌ не даёт edge      │
│ 4. OI × 66 tickers        | supercandles | BR +97%   │ 🟡 BR/CR/AF только  │
│ 5. Order Book Imbalance   | obstats_fo   | Si +39%   │ ✅ работает          │
│ 6. OB Imbalance others    | obstats_fo   | нет данных│ ❌ нет CR/SR данных  │
│ 7. Multi-factor score 15+ | supercandles | −3.3%     │ ❌ не предсказывает  │
│ 8. Intraday 5-min         | supercandles | −3.3%     │ ❌ vol_z медвежий    │
│ 9. Options Put/Call Ratio | options_board| +18%      │ ⚠️ данных 5 дней    │
│ 10. Базовые (форвардные)  | futures_hist | нет OOS   │ ❌ контрактов нет    │
└──────────────────────────────────────────────────────────────────────────────┘

## Лучший портфель (найденный)

┌──────────────────┬──────────────┐
│ Параметр         │ Значение     │
├──────────────────┼──────────────┤
│ Стратегии        │ BR_vol_LONG  │
│                  │ CR_oi        │
│                  │ AF_oi        │
│                  │ Si_imb       │
│ Leverage         │ 1.5x         │
│ Crash protection │ rdc=5%       │
│ Total return     │ +1,043.0%    │
│ Annualized       │ +45.6%       │
│ Max DD           │ -14.1%       │
│ Sharpe (ann)     │ 1.61         │
│ Calmar           │ 3.23         │
│ Walk-forward     │ 4/6 лет +    │
└──────────────────┴──────────────┘

## Выводы

1. **100%+/год при DD ≤ 15% НА MOEX НЕ НАЙДЕНО**
   - 10 направлений исследовано системно
   - 3 работают (AF OI, Si Imbalance, CR OI)
   - Портфель из них даёт +46%/год при DD 14% с 1.5x плечом
   - Для 100%+ нужно >3x плечо, что даёт DD 30%+

2. **Причина фундаментальная**
   - MOEX — не US рынок. Дневные движения 1-3%, нет 10% дней
   - Сигналы дают avg return 0.2-0.5% на сделку
   - Комиссия 0.16-0.4% съедает большую часть
   - Без плеча 3-5x невозможно получить >50% годовых
   - С плечом 3-5x DD неизбежно >20%

3. **Что реально работает**
   - Портфель v2: +46%/год, DD 14%, Calmar 3.23 — профессиональный уровень
   - Для масштабирования: увеличить капитал (не плечо)

## Файлы

─ projects/TQA-MOEX-futures/checkpoint/093-final-verdict.md (new)
─ projects/TQA-MOEX-futures/scripts/moex_portfolio_v2.py (best portfolio)
─ projects/TQA-MOEX-futures/scripts/d1_multifactor_score.py
─ projects/TQA-MOEX-futures/scripts/d2_intraday_5m.py
─ projects/TQA-MOEX-futures/scripts/d3_options_pcr.py
─ projects/TQA-MOEX-futures/reports/night_v3_result.md
