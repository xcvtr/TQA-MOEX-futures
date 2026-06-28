# YUR_net Phase 1: Correlation with Next-Day Return

**Generated:** 2026-06-23

## Summary

| Ticker | Display | Period | N Days | CORR(yur_net_z→ret) | p-value |
|--------|---------|--------|--------|--------------------|---------|
| CR | CR | 2025-01-31–2026-06-10 | 294 | -0.0017 | 0.9770 |
| GL | GL | 2025-01-31–2026-06-11 | 297 | 0.0256 | 0.6610 |
| Si | Si | 2025-01-31–2026-06-10 | 294 | 0.0155 | 0.7915 |
| BR | BR | 2025-01-31–2026-06-11 | 297 | 0.0479 | 0.4110 |
| GD | GOLD | 2025-01-31–2026-06-11 | 297 | 0.0850 | 0.1437 |
| SR | SBRF | 2025-01-31–2026-06-11 | 297 | 0.0106 | 0.8558 |

## Detailed Correlation by Ticker

### CR (CR)

- **Period:** 2025-01-31 – 2026-06-10 (294 trading days)
- **yur_net:** mean=-4757970, σ=1320181, range=[-7182075, -511808]

| Feature | CORR | p-value |
|---------|------|--------|
| yur_net_z → next_day_ret | -0.0017 | 0.9770 |
| yur_buy_z → next_day_ret | 0.0115 | — |
| yur_sell_z → next_day_ret | 0.0370 | — |
| fiz_net_z → next_day_ret | 0.0017 | — |

**Correlation by year:**
| Year | r | p | n |
|------|---|----|----|
| 2025 | 0.0105 | 0.8874 | 184 |
| 2026 | -0.0393 | 0.6832 | 110 |

**Win rates (yur_net_z → next day, direction by sign):**
| Threshold | LONG WR | LONG n | LONG avg_ret | SHORT WR | SHORT n | SHORT avg_ret |
|-----------|---------|--------|-------------|----------|---------|--------------|
| th=1.5 | 47.6% | 42 | 0.0011 | 62.1% | 29 | -0.0025 |
| th=2.0 | 55.0% | 20 | 0.0038 | 54.5% | 11 | 0.0047 |
| th=2.5 | 50.0% | 4 | -0.0079 | 0.0% | 0 | 0.0000 |
| th=3.0 | 0.0% | 0 | 0.0000 | 0.0% | 0 | 0.0000 |

**Multi-bar correlation (yur_net_z → ret_fwd+N):**
| Lag | r | p | n |
|-----|----|----|-----|
| t+1 | -0.0016 | 0.9783 | 293 |
| t+2 | 0.0282 | 0.6313 | 292 |
| t+3 | -0.0023 | 0.9695 | 291 |
| t+4 | 0.0360 | 0.5420 | 290 |
| t+5 | 0.0053 | 0.9285 | 289 |

### GL (GL)

- **Period:** 2025-01-31 – 2026-06-11 (297 trading days)
- **yur_net:** mean=-167226, σ=96641, range=[-473321, -6144]

| Feature | CORR | p-value |
|---------|------|--------|
| yur_net_z → next_day_ret | 0.0256 | 0.6610 |
| yur_buy_z → next_day_ret | 0.0231 | — |
| yur_sell_z → next_day_ret | 0.0127 | — |
| fiz_net_z → next_day_ret | -0.0256 | — |

**Correlation by year:**
| Year | r | p | n |
|------|---|----|----|
| 2025 | 0.0057 | 0.9388 | 184 |
| 2026 | 0.0463 | 0.6266 | 113 |

**Win rates (yur_net_z → next day, direction by sign):**
| Threshold | LONG WR | LONG n | LONG avg_ret | SHORT WR | SHORT n | SHORT avg_ret |
|-----------|---------|--------|-------------|----------|---------|--------------|
| th=1.5 | 55.0% | 40 | -0.0025 | 55.6% | 36 | 0.0048 |
| th=2.0 | 46.7% | 15 | -0.0113 | 62.5% | 16 | 0.0002 |
| th=2.5 | 0.0% | 0 | 0.0000 | 66.7% | 6 | 0.0006 |
| th=3.0 | 0.0% | 0 | 0.0000 | 0.0% | 0 | 0.0000 |

**Multi-bar correlation (yur_net_z → ret_fwd+N):**
| Lag | r | p | n |
|-----|----|----|-----|
| t+1 | 0.0254 | 0.6629 | 296 |
| t+2 | -0.0587 | 0.3149 | 295 |
| t+3 | -0.0814 | 0.1637 | 294 |
| t+4 | -0.0308 | 0.5995 | 293 |
| t+5 | 0.0021 | 0.9713 | 292 |

### Si (Si)

- **Period:** 2025-01-31 – 2026-06-10 (294 trading days)
- **yur_net:** mean=-1160258, σ=392383, range=[-1908312, -355934]

| Feature | CORR | p-value |
|---------|------|--------|
| yur_net_z → next_day_ret | 0.0155 | 0.7915 |
| yur_buy_z → next_day_ret | 0.0409 | — |
| yur_sell_z → next_day_ret | 0.0257 | — |
| fiz_net_z → next_day_ret | -0.0155 | — |

**Correlation by year:**
| Year | r | p | n |
|------|---|----|----|
| 2025 | 0.0332 | 0.6549 | 184 |
| 2026 | -0.0519 | 0.5902 | 110 |

**Win rates (yur_net_z → next day, direction by sign):**
| Threshold | LONG WR | LONG n | LONG avg_ret | SHORT WR | SHORT n | SHORT avg_ret |
|-----------|---------|--------|-------------|----------|---------|--------------|
| th=1.5 | 38.9% | 72 | -0.0053 | 65.0% | 20 | -0.0022 |
| th=2.0 | 40.7% | 27 | -0.0029 | 63.6% | 11 | -0.0042 |
| th=2.5 | 22.2% | 9 | -0.0097 | 83.3% | 6 | -0.0085 |
| th=3.0 | 33.3% | 3 | 0.0010 | 100.0% | 3 | -0.0100 |

### BR (BR)

- **Period:** 2025-01-31 – 2026-06-11 (297 trading days)
- **yur_net:** mean=-68694, σ=76951, range=[-371115, 86148]

| Feature | CORR | p-value |
|---------|------|--------|
| yur_net_z → next_day_ret | 0.0479 | 0.4110 |
| yur_buy_z → next_day_ret | 0.0795 | — |
| yur_sell_z → next_day_ret | 0.0081 | — |
| fiz_net_z → next_day_ret | -0.0479 | — |

**Correlation by year:**
| Year | r | p | n |
|------|---|----|----|
| 2025 | 0.0680 | 0.3592 | 184 |
| 2026 | 0.0490 | 0.6061 | 113 |

**Win rates (yur_net_z → next day, direction by sign):**
| Threshold | LONG WR | LONG n | LONG avg_ret | SHORT WR | SHORT n | SHORT avg_ret |
|-----------|---------|--------|-------------|----------|---------|--------------|
| th=1.5 | 52.4% | 42 | -0.0008 | 35.5% | 31 | 0.0084 |
| th=2.0 | 63.2% | 19 | 0.0046 | 28.6% | 14 | 0.0097 |
| th=2.5 | 75.0% | 4 | 0.0150 | 0.0% | 0 | 0.0000 |
| th=3.0 | 0.0% | 0 | 0.0000 | 0.0% | 0 | 0.0000 |

### GOLD (GD)

- **Period:** 2025-01-31 – 2026-06-11 (297 trading days)
- **yur_net:** mean=-62433, σ=27374, range=[-117212, 23125]

| Feature | CORR | p-value |
|---------|------|--------|
| yur_net_z → next_day_ret | 0.0850 | 0.1437 |
| yur_buy_z → next_day_ret | 0.0306 | — |
| yur_sell_z → next_day_ret | -0.0190 | — |
| fiz_net_z → next_day_ret | -0.0850 | — |

**Correlation by year:**
| Year | r | p | n |
|------|---|----|----|
| 2025 | 0.1017 | 0.1695 | 184 |
| 2026 | 0.0756 | 0.4263 | 113 |

**Win rates (yur_net_z → next day, direction by sign):**
| Threshold | LONG WR | LONG n | LONG avg_ret | SHORT WR | SHORT n | SHORT avg_ret |
|-----------|---------|--------|-------------|----------|---------|--------------|
| th=1.5 | 48.9% | 45 | -0.0055 | 35.7% | 28 | 0.0022 |
| th=2.0 | 45.5% | 22 | -0.0065 | 45.5% | 11 | -0.0062 |
| th=2.5 | 40.0% | 5 | -0.0017 | 60.0% | 5 | -0.0131 |
| th=3.0 | 0.0% | 0 | 0.0000 | 0.0% | 0 | 0.0000 |

### SBRF (SR)

- **Period:** 2025-01-31 – 2026-06-11 (297 trading days)
- **yur_net:** mean=-100961, σ=37644, range=[-188424, -18147]

| Feature | CORR | p-value |
|---------|------|--------|
| yur_net_z → next_day_ret | 0.0106 | 0.8558 |
| yur_buy_z → next_day_ret | 0.0479 | — |
| yur_sell_z → next_day_ret | 0.0263 | — |
| fiz_net_z → next_day_ret | -0.0106 | — |

**Correlation by year:**
| Year | r | p | n |
|------|---|----|----|
| 2025 | 0.0026 | 0.9716 | 184 |
| 2026 | 0.0615 | 0.5174 | 113 |

**Win rates (yur_net_z → next day, direction by sign):**
| Threshold | LONG WR | LONG n | LONG avg_ret | SHORT WR | SHORT n | SHORT avg_ret |
|-----------|---------|--------|-------------|----------|---------|--------------|
| th=1.5 | 44.4% | 36 | -0.0005 | 53.8% | 26 | 0.0017 |
| th=2.0 | 38.5% | 13 | -0.0003 | 66.7% | 12 | -0.0004 |
| th=2.5 | 40.0% | 5 | -0.0029 | 100.0% | 3 | -0.0072 |
| th=3.0 | 25.0% | 4 | -0.0047 | 0.0% | 0 | 0.0000 |

## Conclusion

- Tickers with |r| > 0.15: None

*Note: supercandles_fo_v3 data starts 2025-01-03. CR/GL futoi starts 2022-04.*