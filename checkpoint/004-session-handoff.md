# Session Handoff — 2026-06-08 03:50

**Status:** Session ended by user (/reset). Continue in next session.

## What was completed

1. **FIZ/YUR asymmetry scan (5m):** All 64 tickers analyzed. Key finding: FIZ and YUR are ALWAYS counterparties on 5m (fiz_net ≈ -yur_net).
2. **Multi-strategy scan (22 tickers × 4 strategies):** Only Volume Surge + Divergence shows edge. HS/KC pass WR≥55%, HY/DX borderline.
3. **PnL simulation:** With MOEX commissions (2 руб) + 1 tick slippage + leverage 5-10x → HS 55%/год, KC 38%/год. Net PF ≈ gross PF - 10-15%.
4. **Trading bot deployed:**
   - `trading_bot/engine.py` — z-scores + detect_signals
   - `trading_bot/scanner.py` — load_data + scan_all  
   - `trading_bot/tracker.py` — paper positions + PnL
   - `trading_bot/alerts.py` — telegram-ready alerts
   - `trading_bot/cron_scanner.py` — entry point
   - `trading_bot/__init__.py` — config (HS/KC/DX/HY tickers)
5. **Cron:** `*/15 7-18 * * 1-5` — scans every 15 min during MOEX hours

## Priority tasks (next session, 6 hours)

### 1. Walk-forward optimization 🔥
- Implement expanding window optimization per ticker
- Find optimal vol_z/div_z/horizon per ticker
- Validate on out-of-sample period
- Delegate to OpenCode

### 2. Signal quality filters 🔥
- Regime filter: only trade when market is trending (ADX > 20 or similar)
- Volume profile: filter out low-confidence volume surges
- FIZ/YUR momentum: add fiz_z rate-of-change condition
- Goal: push WR from 50-53% to 55%+ on BM, CC, RN, NG

### 3. Correlation + portfolio 
- Check signal overlap between HS/KC/DX/HY
- If uncorrelated → trade them together for smoother equity
- Build equity curve with equal weight

### 4. Dashboard  
- `trading_bot/dashboard.py` on port 5080
- Show: open positions, trades log, equity curve, last scan
- Use http.server (no extra deps)

### 5. BM + CC + RN re-scan
- These had PF 1.6-1.7 but WR 50-52%
- Try tighter vol_z thresholds + different horizons
- Maybe add regime filter to push WR above 55%

## Critical info

**DB:** host=10.0.0.60, port=5432, dbname=moex, user=postgres, password=postgres
**Tables:** moex_prices_5m_oi (5m FIZ/YUR), moex_prices_5m (5m OHLCV)
**Project:** /home/user/projects/TQA-MOEX/trading_bot/
**Venv:** source ~/venvs/tqa/main/bin/activate

**Working tickers (param: vol_z, div_z, horizon, go):**
- HS: 2.75, 1.5, 12, 5000
- KC: 2.0, 2.0, 24, 2500 (tick_rub=80, lot=100)
- DX: 3.0, 1.5, 48, 3000
- HY: 2.5, YUR-DOM r1.5, 48, 3000

**Delegation pattern:**
- Use `delegate_task(tasks=[...])` with full context (DB schema, file paths, code reference)
- Each task writes independent files
- Verify imports: `python -c "from trading_bot.X import *; print('OK')"

**Memory full** — cannot add more entries. Use session_search for recall.
