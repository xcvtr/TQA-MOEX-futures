# TRIZ Research Summary — 5 Directions Edge Search on MOEX Futures
**Date:** 2026-06-10  
**Data:** PostgreSQL moex_prices_5m (5m bars, 65 tickers, 2023–2026)  
**Methodology:** TRIZ framework per direction, BarLevelPortfolio validation

---

## Executive Summary

| Direction | Edge Found? | Best Result | Calmar | Actionable |
|-----------|-------------|-------------|--------|------------|
| 1. Cross-market | ✅ YES | GL-ED reversion 58.7% (L20) | 0.14 | Marginal |
| 2. Futures curve | ✅ **STRONG** | BR contango→up 77% (10d) | — | **YES** |
| 3. Macro fundamental | ❌ NO | Trend persistence, no reversion | — | No |
| 4. ML gradient boosting | ✅ YES | SR AUC 0.567 (next_5_bar) | — | Potential |
| 5. DOM order book | ❌ NO | Only 2 weeks Jan 2024 data | — | No |

---

## Direction 1: Cross-Market Correlation Mean-Reversion

### ПРОТИВОРЕЧИЕ
Точность входа ↔ Задержка сигнала. Корреляция нестабильна.

### ИКР
Система сама находит статистически значимые расхождения с win rate > 55%.

### РЕШЕНИЕ
Rolling 60-bar correlation на 8 тикерах (Si, BR, RI, GL, ED, FF, AU, CNYRUBF), divergence > 2σ.

### РЕЗУЛЬТАТ
**20 edges detected** (mostly low event count). Top reliable (>100 events):

| Pair | Window | Win Rate | Events |
|------|--------|----------|--------|
| GL-ED | L20 | **58.7%** | 150 |
| GL-ED | L10 | **58.0%** | 150 |
| BR-ED | L10 | **56.7%** | 157 |
| Si-CNYRUBF | L5 | **56.5%** | 418 |
| GL-CNYRUBF | L10 | **55.5%** | 375 |
| RI-CNYRUBF | L10 | **55.2%** | 406 |
| BR-GL | L10 | **55.3%** | 152 |

**BarLevelPortfolio test:** Calmar = 0.14 (100k → 102.9k, +2.9%, DD 20.7%)  
**Verdict:** Marginal edge, insufficient for Calmar > 1.0.

---

## Direction 2: Futures Curve Contango/Backwardation ⭐ BEST

### ПРОТИВОРЕЧИЕ
Фьючерсная кривая содержит информацию, но не используется в стратегиях.

### ИКР
Стратегия на основе экстремумов базиса предсказывает движение цены.

### РЕШЕНИЕ
Rolling basis (front month - back month) / front month. Проверка predictive power.

### РЕЗУЛЬТАТ

**BR (Brent) — STRONGEST EDGE IN ALL DIRECTIONS:**

| Signal | Threshold | Horizon | Win Rate | Avg Return | Sharpe |
|--------|-----------|---------|----------|------------|--------|
| **Contango → Long** | +0.001 | **10 days** | **77.0%** | +3.92% | 0.53 |
| **Contango → Long** | +0.001 | **21 days** | **73.3%** | +11.38% | 0.76 |
| Contango → Long | +0.01 | 10 days | 72.3% | +3.32% | 0.48 |
| Contango → Long | +0.02 | 10 days | 67.7% | +2.79% | 0.42 |

- BR contango 80.1% of days — persistent structural condition
- More contango → stronger upward price movement
- Best at 10-21 day horizons

**RI (RTS):**
| Signal | Threshold | Horizon | Win Rate |
|--------|-----------|---------|----------|
| Backwardation → Short | -0.005 | 3 days | **75.0%** |
| Backwardation → Short | -0.001 | 3 days | 66.7% |

**Si (USD/RUB):** Weak signal (backwardation -0.02, 3d: 55.6%)

**Verdict:** ✅ BR contango → long is the strongest candidate for Calmar > 1.0 strategy.

---

## Direction 3: Macro Fundamental Analysis

### ПРОТИВОРЕЧИЕ
Momentum ↔ Mean reversion на weekly timeframe.

### РЕШЕНИЕ
Daily OHLCV → weekly returns, cross-correlation, z-score extremes.

### РЕЗУЛЬТАТ

**Cross-correlation (predictive):**
- RI → IMOEXF: ρ = +0.72 (RTS leads MOEX index)
- Si → RI: ρ = -0.45 (USD/RUB weakness → RTS strength)
- BR → Si: ρ = +0.14 (Brent → Ruble weakening)

**Momentum persistence (no mean reversion):**
- After >+3% weeks: avg next week = +3.3% to +4.7% (trend continues)
- After <-3% weeks: avg next week = -3.2% to -4.7% (trend continues)
- **0% reversion** for Si, BR, RI z-score extremes

**Verdict:** ❌ No edge. Weekly timeframe shows trend persistence, not reversion.

---

## Direction 4: ML Gradient Boosting

### ПРОТИВОРЕЧИЕ
Слабый сигнал скрыт шумом. ML-модели переобучаются.

### ИКР
GradientBoostingClassifier с AUC > 0.55 на хотя бы одном инструменте.

### РЕШЕНИЕ
15 features (MA z-scores, ATR, ADX, Volume/OI ratios, RSI, BB, returns, time features).

### РЕЗУЛЬТАТ

**7 edges detected across 5 tickers:**

| Ticker | Target | AUC | Accuracy | Precision | Recall |
|--------|--------|-----|----------|-----------|--------|
| **SR** | next_5_bar | **0.567** | 0.550 | 0.543 | 0.498 |
| **GZ** | next_5_bar | **0.565** | 0.550 | 0.531 | 0.429 |
| **GZ** | next_bar | **0.564** | 0.554 | 0.547 | 0.269 |
| **SR** | next_bar | **0.559** | 0.545 | 0.538 | 0.291 |
| **BR** | next_bar | **0.554** | 0.549 | 0.530 | 0.214 |
| **RI** | next_bar | **0.551** | 0.546 | 0.522 | 0.223 |
| **SBERF** | next_5_bar | **0.550** | 0.540 | 0.533 | 0.394 |

- Si (USD/RUB): weakest — pure noise (AUC ~0.52)
- SR (Sberbank) and GZ (Gazprom) strongest
- Low recall (0.21-0.50) but stable precision (>0.52)
- No look-ahead bias: strict time-based split

**Verdict:** ✅ Real but weak edge. AUC 0.55-0.57 is above noise floor but below deployable threshold. Needs feature improvement or ensemble.

---

## Direction 5: DOM / Order Book Analysis

### ПРОТИВОРЕЧИЕ
Bid/ask imbalance noisy, latency-critical, microstructure edge vs aggregation.

### ИКР
DOM features predict 5-min bar direction with >55% accuracy.

### РЕШЕНИЕ
Aggregate DOM snapshots to 5-min bars, compute imbalance + cluster volume.

### РЕЗУЛЬТАТ

| Ticker | Bars | Imbalance Acc | Cluster Imb Acc | Baseline | Edge? |
|--------|------|--------------|----------------|----------|-------|
| GAZR | 2,144 | 50.2% | 52.5% | 50.2% | ❌ |
| SBRF | 1,569 | 50.5% | 49.0% | 50.1% | ❌ |
| Si | 263 | — | — | — | NO DATA |

- Only 3 tickers with DOM data (GAZR, SBRF, Si)
- Data limited to ~2 weeks in January 2024
- Combined features accuracy: 53.6% (GAZR) — below threshold
- **Verdict:** ❌ No edge + insufficient data for reliable conclusion.

---

## Overall Conclusions

### Best Strategy Candidates

1. **🥇 BR Contango → Long (Direction 2):** 77% win rate at 10 days, Sharpe 0.53-0.76. Most actionable signal from this research. Recommended for further development with proper risk management.
2. **🥈 GL-ED Reversion (Direction 1):** 58.7% win rate over 150 events. Marginal but viable as diversification.
3. **🥉 ML on SR/GZ (Direction 4):** AUC 0.567. Weak but demonstrates feature engineering value.

### Why No Calmar > 1.0 Found
- Cross-market edges are real but low signal-to-noise for bar-level sim
- Best edges (BR contango) operate on daily timeframe, not directly compatible with 5m BarLevelPortfolio
- DOM data insufficient for robust analysis
- Weekly macro shows trend persistence, not reversion

### Recommendations
1. Build a dedicated BR contango strategy (daily bars, 10-day hold, 77% win rate)
2. Add BR basis as a regime filter to existing OI divergence strategies
3. Re-run ML with ensemble methods and walk-forward optimization
4. Collect more DOM data (months, not weeks) and re-test
5. Combine Direction 1 + Direction 4 signals for ensemble portfolio

---

## TRIZ Master Diagram

```
                    ┌─────────────────────────────────────┐
                    │    ПРОТИВОРЕЧИЕ (5 направлений)      │
                    │   Точность vs Скорость / Сигнал vs   │
                    │   Шум / Momentum vs Reversion        │
                    └──────────┬──────────────────────────┘
                               │
                    ┌──────────▼──────────────────────────┐
                    │         ИКР (для каждого)            │
                    │   Calmar > 1.0 стратегия на MOEX     │
                    └──────────┬──────────────────────────┘
                               │
                    ┌──────────▼──────────────────────────┐
                    │         РЕШЕНИЕ                      │
                    │   5 параллельных направлений         │
                    └──────────┬──────────────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
   Cross-Market          Futures Curve              ML
   GL-ED 58.7%        BR Contango 77%          SR AUC 0.567
   (marginal)           (STRONGEST)              (promising)
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               ▼
                    ┌──────────────────────────┐
                    │     РЕЗУЛЬТАТ             │
                    │  BR Contango: Calmar ??? │
                    │  (daily TF, 77% win)     │
                    └──────────────────────────┘
```

**Report generated at 2026-06-10 by TRIZ 5-directions research**
