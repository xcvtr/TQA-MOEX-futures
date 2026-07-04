---
title: "Final optimization & paper trader config"
checkpoint: 145
date: 2026-07-04
tags: [checkpoint, tqa-moex-futures, paper-trader]
---

# Checkpoint 145 — Final optimization & paper trader config

## PnL formula (final, verified)
```
pnl = (exit - entry) / ms * sp * lot * pct * contracts - 4 * contracts
```
- `lot` IS required (step_price per unit, lot converts to contract)
- `pct` per ticker in PG `ticker_specs.pct` (Si=1.0, GZ=1.0, RN=1.0, GD=1.0)
- `contracts` from PG `futures.portfolio` (1 for all tickers)

## Specs (corrected)
- Si: sp=0.001, ms=1, lot=1000, pct=1.0 → 1pt=1.0 ₽
- GZ: sp=1, ms=1, lot=100, pct=1.0 → 1pt=100 ₽
- RN: sp=1, ms=1, lot=100, pct=1.0 → 1pt=100 ₽
- GD: sp=7.7, ms=0.1, lot=1, pct=1.0 → 1pt=77 ₽

## Reinvestment analysis (all configs with 1% risk, 200K)
- NO_GZ: DD 99-158% ❌
- ALL + MAX=5-10: DD 37-40% → MTM DD ~60-80% ❌
- **Fixed 1 contract: DD ≤ 10% ✅ → MTM DD ~15% ≤ 20% ✅**

## Paper trader config (final)
- Capital: 200K (PG paper_state)
- Portfolio: GZ(1) Si(1) RN(1) GD(0 — ГО too high) (PG portfolio)
- Strategies: Stop Hunt (SHORT+LONG)
- Contracts: 1 fixed (no reinvestment)
- Cron: system ```/5 15-23 * * 1-5``` (10:00-18:45 MSK)
- MTM DD target: ≤20% ✅ (estimated 10-15% with 1 contract)

## Results (1 contract, 18 months)
- +7.3M PnL, ~1,000% CAGR
- 56.4% WR, 2.03 PF
- Si: +2.7M, 53.9% WR
- GZ: +554K, 54.9% WR
- RN: +1.1M, 58.3% WR
- GD: +3.0M, 59.3% WR
