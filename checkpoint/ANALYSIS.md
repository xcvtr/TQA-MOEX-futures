# Whale Detector V8 — Analysis

## Results (as of 2026-06-02)

### Si (USD/RUB) — main instrument

| Metric | Value |
|---|---|
| Signals | 18 over 3 years (~6/yr) |
| Winrate | **78%** (14/18) |
| Avg win | +1.29% |
| Avg loss | -0.91% |
| Profit Factor | **4.95** |
| Compound return | **+15.2% over 3 years** |
| Max drawdown | -2.26% |

### All instruments (combined equity)

- Si: 18 sig, 78% WR
- BR (Brent): 15 sig, 53% WR
- NG (Gas): 38 sig, 66% WR
- GD (Gold): 4 sig, 50% WR
- **Combined return: ~+100% over 3 years** (all instruments together)

## Verdict

**The strategy works.** It is a conservative OI-pattern-based approach
(FIZ_DROP_3D, YUR_LOAD_5D, FIZ_FLEE_3D).

### Strengths
- High accuracy: 78% on Si, 66% on NG
- Profit Factor 4.95 — excellent risk/reward ratio
- Minimal drawdowns (max -2.26%)
- Stable: no losing years

### Weaknesses
- Few trades (~6/yr per instrument)
- ~5% annual return on a single instrument — modest
- All signals LONG — no profit from downtrends

### Recommendations
- Trade multiple instruments simultaneously (Si + NG + BR) for ~26% annual
- Use as a conservative portfolio allocation
- Add SHORT signals for bear markets
- Increase signal frequency with more sensitive thresholds

## Data sources
- Prices: Alor OpenAPI to moex_prices_5m (5m bars to D1 OHLC)
- OI: MOEX ISS API to openinterest_moex
- Dashboard: http://10.0.0.60:5055/dashboard.html
