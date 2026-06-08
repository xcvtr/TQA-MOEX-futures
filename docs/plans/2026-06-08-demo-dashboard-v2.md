# MOEX Demo Dashboard v2 — Open Architecture Design + Implementation

**Goal:** Replace the current `http.server` dashboard with a FastAPI + Plotly interactive dashboard that supports multiple strategies (now and future) and potentially multiple markets.

**Design principle:** Open architecture — adding a new strategy or market should NOT require dashboard code changes.

---

## Architecture Overview

```
trading_bot/dashboard_v2/
├── __init__.py          — app factory, creates FastAPI app
├── serve.py             — entry point: uvicorn.run()
├── core/
│   ├── registry.py      — Strategy & Market registries (THE KEY FILE)
│   ├── models.py        — Signal, Trade, Position data models
│   └── statistics.py    — WR, PF, DD, equity curve (shared)
├── adapters/
│   ├── moex_adapter.py  — MOEX OHLCV + OI loader
│   ├── moex_strategies.py — auto-registers all 4 existing strategies
│   ├── crypto_adapter.py  — Crypto loader (FUTURE — stub)
│   └── forex_adapter.py   — Forex loader (FUTURE — stub)
├── routers/
│   ├── live.py          — GET /api/live/signals, /api/live/positions
│   ├── backtest.py      — GET /api/backtest/results, /api/backtest/strategies
│   ├── portfolio.py     — GET /api/portfolio/stats, /api/portfolio/equity
│   └── data.py          — GET /api/bars, /api/freshness
└── frontend/
    └── index.html       — single-page app with Plotly.js
```

---

## Core: Registry (the open architecture key)

**`trading_bot/dashboard_v2/core/registry.py`**

```python
"""Strategy and Market registries — the open architecture."""

# ── Strategy Registry ──────────────────────────────────────────────

_strategies = {}  # name -> StrategyInfo

class StrategyInfo:
    """Everything needed to use a strategy."""
    def __init__(self, name, display_name, detect_fn, default_config, tickers,
                 needs_oi=False, description=""):
        self.name = name
        self.display_name = display_name
        self.detect_fn = detect_fn      # function(symbol, data, config) -> [signals]
        self.default_config = default_config
        self.tickers = tickers          # dict of {ticker: {go, tick_rub, ...}}
        self.needs_oi = needs_oi        # does it need OI data?
        self.description = description

def register_strategy(info: StrategyInfo):
    """Register a strategy. Called at import time by each strategy module."""
    _strategies[info.name] = info

def get_strategy(name: str) -> StrategyInfo:
    return _strategies.get(name)

def list_strategies() -> list[StrategyInfo]:
    return list(_strategies.values())


# ── Market Registry ────────────────────────────────────────────────

_markets = {}  # name -> MarketInfo

class MarketInfo:
    """Data source information for a market."""
    def __init__(self, name, display_name, db_config, load_bars_fn,
                 load_oi_fn=None, symbols=None):
        self.name = name
        self.display_name = display_name
        self.db_config = db_config
        self.load_bars_fn = load_bars_fn
        self.load_oi_fn = load_oi_fn
        self.symbols = symbols or []

def register_market(info: MarketInfo):
    _markets[info.name] = info

def list_markets():
    return list(_markets.values())


# ── Strategy-market mapping ────────────────────────────────────────

# Each strategy belongs to a market
_strategy_market = {}  # strategy_name -> market_name

def map_strategy_to_market(strategy_name, market_name):
    _strategy_market[strategy_name] = market_name

def get_strategies_for_market(market_name):
    return [s for s_name, s in _strategies.items()
            if _strategy_market.get(s_name) == market_name]
```

### How to add a new strategy (user perspective):

```python
# trading_bot/dashboard_v2/adapters/moex_strategies.py
from ..core.registry import register_strategy, StrategyInfo, map_strategy_to_market
from trading_bot.vwap_engine import detect_vwap_signals, VWAP_TICKERS
from trading_bot.reversion_engine import detect_mean_reversion_signals
# ... etc

# Auto-register at import time
register_strategy(StrategyInfo(
    name='vwap',
    display_name='VWAP Deviation Reversion',
    detect_fn=detect_vwap_signals,
    default_config={'dev_thresh': 2.0, 'horizon': 12, 'vwap_window': 20},
    tickers=VWAP_TICKERS,
    needs_oi=False,
    description='Price deviation from VWAP > 2 ATR → reversion',
))
map_strategy_to_market('vwap', 'moex')
```

**To add a new strategy later:**
```python
# Just create a module that calls register_strategy() at import.
# Zero changes to dashboard code.
```

---

## Frontend Plan

Single `index.html` with Plotly.js (like TQA-FOREX). Tabs:

### Tab 1: 📊 Live
- **Left panel:** ticker selector + timeframe selector (5m/15m/H1/H4)
- **Main chart:** OHLCV candlestick (Plotly) with volume subplot
- **Signals overlay:** colored markers on chart (🟢 LONG, 🔴 SHORT) from all strategies
- **Right panel:** active positions table, last 10 signals table

### Tab 2: 📈 Backtest
- **Strategy selector** (VS, Reversion, OB, VWAP — auto-detected from registry)
- **Ticker selector** — filtered by strategy's tickers
- **Chart:** equity curve (Plotly), WR bar chart
- **Stats table:** n, WR%, PF%, DD%, avg_ret

### Tab 3: 🏆 Portfolio
- **Combined equity curve** — all 4 strategies on one chart with colored lines + legend
- **Donut chart** — trade distribution by strategy
- **Rolling WR** — last 50 trades sparkline
- **Metrics cards:** total PnL, WR, PF, open positions, capital used

### Tab 4: 📡 Data
- **Freshness table:** ticker → last bar → days behind
- **Signal count table:** strategy → ticker → signals today
- **DB status:** connected, last update

---

## API Routes

| Method | Path | Returns |
|--------|------|---------|
| GET | `/` | index.html |
| GET | `/api/strategies` | list of registered strategies |
| GET | `/api/markets` | list of registered markets |
| GET | `/api/bars?symbol=X&tf=H1&days=5` | OHLCV bars for chart |
| GET | `/api/live/signals` | recent signals (last 1h) |
| GET | `/api/live/positions` | open positions |
| GET | `/api/portfolio/stats` | portfolio stats (WR, PF, PnL) |
| GET | `/api/portfolio/equity` | equity curves (all strategies + combined) |
| GET | `/api/backtest/strategies` | list strategies available for backtest |
| GET | `/api/backtest/run?strategy=vwap&ticker=GZ&params=...` | run backtest, return equity curve |
| GET | `/api/data/freshness` | data freshness per ticker |

---

## Tasks for OpenCode

### Task 1: Create core/ directory
- `trading_bot/dashboard_v2/core/__init__.py`
- `trading_bot/dashboard_v2/core/registry.py` (as above)
- `trading_bot/dashboard_v2/core/models.py` (Signal, Position, Trade dataclasses)
- `trading_bot/dashboard_v2/core/statistics.py` (compute_stats, equity_curve, rolling_wr)

### Task 2: Create MOEX adapter + strategy registration
- `trading_bot/dashboard_v2/adapters/__init__.py`
- `trading_bot/dashboard_v2/adapters/moex_adapter.py` — loads OHLCV + OI from moex DB
- `trading_bot/dashboard_v2/adapters/moex_strategies.py` — registers all 4 strategies (VS, Reversion, OB, VWAP)
- `trading_bot/dashboard_v2/adapters/crypto_adapter.py` — **stub** with comment "TODO: register crypto strategies"
- `trading_bot/dashboard_v2/adapters/forex_adapter.py` — **stub** with comment "TODO: register forex strategies"

### Task 3: Create routers
- `trading_bot/dashboard_v2/routers/__init__.py`
- `trading_bot/dashboard_v2/routers/live.py` — live signals + positions
- `trading_bot/dashboard_v2/routers/backtest.py` — backtest execution using existing strategy functions
- `trading_bot/dashboard_v2/routers/portfolio.py` — portfolio aggregation across strategies
- `trading_bot/dashboard_v2/routers/data.py` — bar data, freshness

### Task 4: Create FastAPI app + frontend
- `trading_bot/dashboard_v2/__init__.py` — app factory, register routers, CORS
- `trading_bot/dashboard_v2/serve.py` — uvicorn entry point
- `trading_bot/dashboard_v2/frontend/index.html` — single-page Plotly.js dashboard with 4 tabs

### Task 5: Create backtest runner
- Uses registry to discover strategies
- For each strategy+ticker+params, run the detect function on full data
- 70/30 time split for out-of-sample validation
- Returns equity curve + stats for display

---

## Open Architecture: How to Add Things

### Adding a new strategy (e.g., "Retail Trap"):
```python
# 1. Create strategy module
# 2. Import and register:
from ..core.registry import register_strategy, StrategyInfo
register_strategy(StrategyInfo(
    name='retail_trap',
    display_name='Retail Trap (Fiz Extremes)',
    detect_fn=detect_retail_trap_signals,
    default_config={'fiz_z_thresh': 1.5, 'horizon': 12},
    tickers=RETAIL_TRAP_TICKERS,
    needs_oi=True,
))
map_strategy_to_market('retail_trap', 'moex')
# 3. Dashboard automatically shows it in all views ✅
```

### Adding a new market (e.g., Crypto):
```python
# 1. Create crypto_adapter.py with load_bars function
# 2. Register:
from ..core.registry import register_market, MarketInfo
register_market(MarketInfo(
    name='crypto',
    display_name='Crypto (TQA-crypto)',
    db_config={'host': '10.0.0.64', 'dbname': 'crypto', ...},
    load_bars_fn=load_crypto_bars,
    symbols=['BTCUSDT', 'ETHUSDT', ...],
))
# 3. Create crypto_strategies.py — register strategies for this market
# 4. Dashboard gets a market selector dropdown ✅
```

---

## Data Flow

```
User selects ticker + strategy
        ↓
FastAPI router → adapter.load_bars(ticker, days) → psycopg2 → OHLCV data
        ↓
strategy.detect_fn(symbol, data, config) → signals
        ↓
Router returns {bars, signals, equity_curve, stats}
        ↓
Plotly.js renders candlestick + markers + equity
```

**NO look-ahead in any detect function** (already verified for all 4).

---

## ⚠️ Critical Rules (same as always)

1. **NO look-ahead.** All indicators use only `data[:i]`.
2. **SHORT return:** `(entry - exit) / entry * 100`
3. **LONG return:** `(exit - entry) / entry * 100`
4. **Backtest out-of-sample:** last 30% of data by time.
5. **Report all tickers** — not just winners.
6. **DB:** host=10.0.0.64, moex DB, user=postgres, password=***
7. **Everything in ~/projects/TQA-MOEX/**

---

## Regarding Crypto & Forex

**Answer for the user:** The architecture supports them (market adapters), but:

- **Forex** already has its own dashboard (:5052) and signal engine. It doesn't need to be here.
- **Crypto** (TQA-crypto) has its own data pipeline. Could be added as a second market adapter later — register with different DB and strategies.
- **Keep MOEX as the primary demo** — it has the richest data (fiz/yur OI).
- **Crypto can be added in 1 day** when needed: just create `crypto_adapter.py` and register a strategy.

The architecture is ready, but for the demo — start with MOEX only. Adding more markets doesn't increase demo quality, it adds complexity.
