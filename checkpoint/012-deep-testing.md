# Checkpoint 012 — Deep Testing: Walk-Forward + Monte Carlo + Sensitivity

**Дата:** 2026-06-08
**Проект:** TQA-MOEX

## Что сделано

### 7 глубоких тестов всех стратегий
Результаты: `docs/backtest/deep_test_*.txt`

| Тест | Файл |
|:-----|:-----|
| 1. Walk-Forward (4 фолда) | `deep_test_1_walkforward.txt` |
| 2. Return Cross-Check | `deep_test_2_return_crosscheck.txt` |
| 3. Parameter Sensitivity | `deep_test_3_sensitivity.txt` |
| 4. Signal Overlap | `deep_test_4_overlap.txt` |
| 5. Market Regime | `deep_test_5_regime.txt` |
| 6. Monte Carlo | `deep_test_6_monte_carlo.txt` |
| 7. Slippage | `deep_test_7_slippage.txt` |
| Сводка | `deep_test_summary.txt` |

### Ключевые выводы

**VWAP** — единственная стратегия, прошедшая ВСЕ тесты:
- Walk-Forward: WR=55.9%±1.7% (стабильна через 4 фолда)
- Sensitivity: 15/15 конфигураций в пределах 2.1% от базы
- Monte Carlo: p=0.0000 (статистически значима)
- Slippage: 0% влияния (тик пренебрежимо мал)
- Regime: работает во всех режимах, лучше в трендах (57.2%)
- Cross-Check: 0 ошибок

**Reversion** — условно торгуема (NM, 63.2%, но std=±10.9%)

**Остальные — НЕ рекомендуется:**
- VS: WR~48%, std до 36%
- OB: нет сигналов из-за stale данных
- OTC: WR<45%
- Retail Trap: WR<50%
- OI Divergence: редкие сигналы

**Обнаружено:** 40.4% пересечение OTC↔Retail Trap на CNYRUBF

### Дашборд
- v2 (:5090) — FastAPI + Plotly, открытая архитектура
- Вкладки: Live, Backtest, Portfolio, Data
- Результаты тестов отражены на фронтенде (verdicts)

## Файлы

| Файл | Статус |
|:-----|:------:|
| `docs/backtest/deep_test_*.txt` (8 файлов) | ✅ новые |
| `docs/plans/2026-06-08-deep-testing-plan.md` | ✅ новый |
| `trading_bot/dashboard_v2/frontend/index.html` | ✅ обновлён (verdicts) |
