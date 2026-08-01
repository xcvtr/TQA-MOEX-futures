# Checkpoint 199: Честный портфель после фиксов багов, +39.6% ROI

**Дата:** 2026-07-31
**Теги:** checkpoint, portfolio, honest, bug-fixes

## 🚨 Критические баги, найденные и исправленные

### 1. LOOK-AHEAD в Dragon (portfolio_run.py)
- **Было:** `bars_list: dh` (без текущего бара) → вход по цене прошлого бара
- **Стало:** `bars_list: dh + [db]` → вход по close текущего (закрытого) бара
- Эффект: Dragon GD PF 3.55→2.71, PnL +219K→+152K

### 2. COOLDOWN в IR engine (engine.py)
- **Было:** `_cooldown_state[ticker] = cooldown` НЕ устанавливался после сигнала
- **Стало:** устанавливается после return → нет дубликатов в один импульс
- Эффект: все аномальные PF исчезли (SV 3862, Si 944, LKOH 620 — дубликаты)

### 3. ПОВТОРНЫЕ СИГНАЛЫ Dragon (portfolio_run.py)
- **Было:** каждый бар пока close < retrace_low → серии сделок (23:30, 23:40, 19:50...)
- **Стало:** `cd_until = db_idx + 24` — cooldown 24 detect бара после входа
- Эффект: GD 202→77 сделок, NG 2393→736 (90% дохода было от повторных входов!)

### 4. Live ≠ бэктест (paper_trader.py)
- M5 бары → M1 + resample detect tf (как бэктест)
- risk-based sizing из PG

## 📊 Честный портфель (2.5 года, common pool)

| Стратегия | Сделок | WR | PF | PnL |
|:----------|:------:|:--:|:--:|----:|
| IR LKOH | 11 | 100% | — | +8.1K |
| IR SNGP | 12 | 83.3% | 21.3 | +14.4K |
| Dragon GD | 77 | 55.8% | 2.93 | +57.7K |
| Dragon NG | 736 | 41.2% | 1.33 | +39K |
| **ПОРТФЕЛЬ** | **836** | — | — | **+39.6%** |

**Capital:** 200,000 → 279,186
**MTM MDD:** 5.84%
**Cash MDD:** 5.28%

## ⚠️ Честная оценка
- Реальный эдж: ~16%/год (было кажется +633% из-за багов)
- 90% "дохода" Dragon было от повторных входов в один паттерн
- IR на акциях ограничен ликвидностью (1-7 контрактов)
- Лучший драйвер: Dragon GD (WR 55.8%, PF 2.93)

## Файлы
- strategies/dragon/scripts/portfolio_run.py — look-ahead fix + dragon cooldown
- strategies/impulse_return/prod/engine.py — cooldown fix
- strategies/common/paper_trader.py — M1+resample, risk sizing
- run_paper_trader.py — expiry check fix
