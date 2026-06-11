# DOM (Depth of Market) Analysis — 2026-06-10

## TRIZ Direction 5: ПРОТИВОРЕЧИЕ → ИКР → РЕШЕНИЕ → РЕЗУЛЬТАТ

### ПРОТИВОРЕЧИЕ (Contradiction)
Order book data is high-frequency and noisy. Bid/ask imbalance may predict short-term price direction, but the signal-to-noise ratio is low and latency is critical. Aggregating into bars loses microstructure edge, but not aggregating leaves too much noise.

### ИКР (Ideal Final Result)
A DOM-derived feature set that predicts 5-min bar direction with >55% accuracy without look-ahead bias, providing an orthogonal signal to existing price-based strategies.

### РЕШЕНИЕ (Solution)
Aggregate raw DOM snapshots into 5-min windows. Compute bid/ask imbalance and cluster volume near the best prices. Test predictive power via logistic regression and directional correlation. No look-ahead: predictors from bar N predict direction of bar N+1.

### РЕЗУЛЬТАТ (Result)

**ANALYSIS COMPLETE**

### GAZR

- **Status**: ANALYZED
- **Raw DOM rows**: 10,124,269
- **5-min bars**: 2144
- **Date range**: 2024-01-05 18:59:49+00:00 to 2024-01-23 15:52:09+00:00
- **Bid type**: 2, **Ask type**: 1
- **Valid non-flat bars**: 1976
- **Baseline accuracy** (always predict majority): 0.5015
- **Imbalance → direction accuracy**: 0.5020
- **Cluster imbalance → direction accuracy**: 0.5248
- **Combined features accuracy**: 0.5359
- **Corr(imbalance, next return)**: 0.0230
- **Corr(cluster imbalance, next return)**: 0.0727
- **Hit rate (|imbalance| > 0.3)**: 0.5182
- **Logistic coef (imbalance)**: 0.1196
- **Logistic coef (cluster imb)**: 0.2901

  **Cluster Volume Stats:**
  - Avg cluster bid vol: 63377.48
  - Avg cluster ask vol: 41890.55
  - Avg cluster total: 105268.03
  - Avg cluster ratio: 0.6492
  - Avg spread (price units): -8.18

  **❌ NO EDGE** — imbalance does not significantly predict direction

### SBRF

- **Status**: ANALYZED
- **Raw DOM rows**: 10,269,739
- **5-min bars**: 1569
- **Date range**: 2024-01-05 18:59:49+00:00 to 2024-01-18 13:23:56+00:00
- **Bid type**: 2, **Ask type**: 1
- **Valid non-flat bars**: 1482
- **Baseline accuracy** (always predict majority): 0.5013
- **Imbalance → direction accuracy**: 0.5054
- **Cluster imbalance → direction accuracy**: 0.4899
- **Combined features accuracy**: 0.5047
- **Corr(imbalance, next return)**: -0.0361
- **Corr(cluster imbalance, next return)**: 0.0091
- **Hit rate (|imbalance| > 0.3)**: 0.4773
- **Logistic coef (imbalance)**: -0.1492
- **Logistic coef (cluster imb)**: -0.0127

  **Cluster Volume Stats:**
  - Avg cluster bid vol: 66554.01
  - Avg cluster ask vol: 85059.16
  - Avg cluster total: 151613.17
  - Avg cluster ratio: 0.8336
  - Avg spread (price units): -22.35

  **❌ NO EDGE** — imbalance does not significantly predict direction

### Si

- **Status**: NO DATA
- **Raw DOM rows**: 2,243,691
- **5-min bars**: 263
- **Date range**: 2024-01-05 18:59:49+00:00 to 2024-01-09 11:52:05+00:00
- **Reason**: Only 263 bars for Si (min required: 1000)

## Data Sources

- **Primary**: `finam_dom_snapshots_v2` (16,503,903 rows total, 3 tickers)
- **Secondary**: `finam_dom_snapshots` — corrupted (TimescaleDB chunk missing)
- **Host**: 10.0.0.64, db=moex

## Methodology Notes

1. **No look-ahead bias**: For each 5-min bar N, imbalance and cluster volume
   are computed only from snapshots within that bar. The target is bar N+1's direction.
2. **Type mapping**: Determined heuristically (type with lower avg price = bid).
3. **Cluster definition**: Volume within 0.1% of the best bid/ask price.
4. **Logistic regression**: Predicts up/down direction on next bar.
5. **Minimum bars**: 1000 bars required for analysis.
6. **Data period**: Only January 2024 is available — very limited.

## Limitations

1. Only 3 tickers with DOM data (GAZR, SBRF, Si)
2. Data only covers ~2 weeks in January 2024
3. No corresponding price table used — mid-price derived from DOM
4. No volume-weighted or time-weighted imbalance variants tested
5. No consideration of order book depth beyond 0.1% cluster

---
*Generated at 2026-06-11 00:04:30*
