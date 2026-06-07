# TQA-MOEX Trading Bot Improvements

> **For OpenCode:** Implementation plan — 6 tasks below.

**Goal:** Fix critical issues and add defensive features to the MOEX trading bot.

**Architecture:** Single-package `trading_bot/` with engine, 3 strategies (VS, Reversion, OB), paper tracker, dashboard, cron scanner. All data from `moex` DB on 10.0.0.64.

**Tech Stack:** Python 3, psycopg2, http.server, no external deps for dashboard.

---

### Task 1: Clean dead code in `moex_equity_dashboard.py`

**Objective:** Remove duplicate dictionaries (O_DATA defined twice) and orphaned data blocks.

**Files:**
- Modify: `/home/user/projects/TQA-MOEX/moex_equity_dashboard.py`

**Problem:** Lines 32-48 define `O_DATA`, then line 49 **redefines** `O_DATA` (overwrites). The first definition is dead code. Similarly GO_DATA and CHAMPIONS may have stale copies.

**Step 1 — Read the file header and identify dead blocks:**
```bash
cd /home/user/projects/TQA-MOEX
python3 -c "
with open('moex_equity_dashboard.py') as f:
    content = f.read()
# Find duplicate O_DATA definitions
import re
defs = [(m.start(), m.group()) for m in re.finditer(r'^(GO_DATA|O_DATA|CHAMPIONS)\s*=\s*\{', content, re.MULTILINE)]
for start, name in defs:
    print(f'Line {content[:start].count(chr(10))+1}: {name}')
"
```

**Step 2 — Remove lines 32-48 (first O_DATA, kept for reference as second GO_DATA comment):**
Remove the block starting at `O_DATA = {` on line 32 through the closing `}` on line 48 (before the real `O_DATA = {` on line 49). Then verify there's only one `O_DATA =` assignment.

**Step 3 — Also check if GO_DATA on lines 15-31 is shadowed/duplicated by the second block (lines 49-63). If so, keep only GO_DATA (the well-documented one) and CHAMPIONS.**

**Step 4 — Verify:**
```bash
cd /home/user/projects/TQA-MOEX
python3 -c "
with open('moex_equity_dashboard.py') as f:
    content = f.read()
import re
matches = re.findall(r'^(GO_DATA|O_DATA|CHAMPIONS)\s*=\s*\{', content, re.MULTILINE)
print(f'Unique dict assignments: {len(matches)} (expected: 3)')
for m in matches:
    print(f'  {m}')
"
```

**Step 5 — Verify the dashboard still starts:**
```bash
cd /home/user/projects/TQA-MOEX
timeout 5 python3 moex_equity_dashboard.py --help 2>&1 || echo 'help mode works'
```

---

### Task 2: Add stop-loss to tracker.py

**Objective:** Add configurable max_loss (%) per position type. Currently max_loss is hardcoded at -5.0%.

**Files:**
- Modify: `/home/user/projects/TQA-MOEX/trading_bot/tracker.py`

**Problem:** In `check_exits()`, line 264: `max_loss = -5.0` hardcoded. No way to configure per-ticker or per-strategy.

**Step 1 — Add `max_loss` to ticker configs:**
In `__init__.py`, add `'max_loss': -5.0` to each ticker in TICKERS, REVERSION_TICKERS, OB_TICKERS.

In `_ticker_config()` in tracker.py, merge all ticker dicts: `ALL_TICKERS = {**TICKERS, **REVERSION_TICKERS, **OB_TICKERS}` (line 13 must include OB_TICKERS).

**Step 2 — Make `check_exits()` read max_loss from ticker config:**
Change line 264 from:
```python
max_loss = -5.0  # % stop-loss by default
```
to:
```python
max_loss = _ticker_config(pos['symbol']).get('max_loss', -5.0)
```

**Step 3 — Also add trailing stop option (bonus):**
Add a field `trailing_stop = pos.get('trailing_stop')` and if the position has `highest_pnl` tracking, tighten stop as PnL improves.

**Step 4 — Verify:**
```bash
cd /home/user/projects/TQA-MOEX
python3 -c "
from trading_bot.tracker import load_positions, check_exits
# Should load without error
print('tracker imports OK')
"

python3 -c "
from trading_bot import TICKERS
for sym, cfg in TICKERS.items():
    assert 'max_loss' in cfg, f'{sym} missing max_loss'
print(f'{len(TICKERS)} VS tickers have max_loss config')
"
```

---

### Task 3: Fix ticker conflict between Reversion and OB

**Objective:** SBERF appears in BOTH REVERSION_TICKERS and OB_TICKERS. Same for BR, NM, AF. When both strategies signal at the same time, only one opens. Eliminate overlap.

**Files:**
- Modify: `/home/user/projects/TQA-MOEX/trading_bot/__init__.py`

**Step 1 — Read current tickers:**
```bash
cd /home/user/projects/TQA-MOEX
python3 -c "
from trading_bot import REVERSION_TICKERS, OB_TICKERS
rev_set = set(REVERSION_TICKERS.keys())
ob_set = set(OB_TICKERS.keys())
overlap = rev_set & ob_set
print(f'Overlap: {overlap}')
print(f'Would reassign: Reversion={rev_set - ob_set}, OB={ob_set - rev_set}')
"
```

**Step 2 — Split overlap:**
- Reversion keeps: NM (strong reversion) + AF (fits reversion pattern better)
- OB keeps: SBERF (strong OB) + BR (strong OB per backtest checkpoint 007)

Update `REVERSION_TICKERS` in __init__.py:
```python
REVERSION_TICKERS: dict = {
    'NM': {'enabled': True, 'go': 1405, 'tick_rub': 1.0, 'minstep': 1, 'label': 'NM (фьючерс Reversion)'},
    'AF': {'enabled': True, 'go': 7000, 'tick_rub': 0.74, 'minstep': 1, 'label': 'AF (Africa Reversion)'},
}
```

Update `OB_TICKERS` in __init__.py:
```python
OB_TICKERS: dict = {
    'SBERF': {'enabled': True, 'go': 6620, 'tick_rub': 1.0, 'minstep': 1, 'label': 'SBERF (Сбер OB)'},
    'BR': {'enabled': True, 'go': 17228, 'tick_rub': 7.43, 'minstep': 1, 'label': 'BR (Brent OB)'},
}
```

**Step 3 — Update `ALL_TICKERS` in tracker.py:**
Add `OB_TICKERS` to the merge on line 13:
```python
ALL_TICKERS = {**TICKERS, **REVERSION_TICKERS, **OB_TICKERS}
```

**Step 4 — Verify no overlap:**
```bash
cd /home/user/projects/TQA-MOEX
python3 -c "
from trading_bot import REVERSION_TICKERS, OB_TICKERS
assert not (set(REVERSION_TICKERS) & set(OB_TICKERS)), 'Overlap still exists'
print('✅ No overlap')
"
```

---

### Task 4: Cache price data for ADX filter (remove double SELECT)

**Objective:** In `cron_scanner.py`, the ADX filter re-loads data via `load_data(tk, days=30)` on line 102 — but this data was already loaded by the strategy engines. Cache it.

**Files:**
- Modify: `/home/user/projects/TQA-MOEX/trading_bot/cron_scanner.py`

**Step 1 — Build a cache dict before ADX filtering:**
At line 96, before the ADX loop, build:
```python
from trading_bot.scanner import load_data
adx_data_cache = {}
```

**Step 2 — Use cache:**
Change line 102 from:
```python
rows = load_data(tk, days=30)
```
to:
```python
if tk not in adx_data_cache:
    adx_data_cache[tk] = load_data(tk, days=30)
rows = adx_data_cache[tk]
```

**Step 3 — Also pre-load for reversion and OB tickers that are not in SCAN_SYMBOLS:**
Before the ADX filter, pre-load data for any ticker in REVERSION_TICKERS or OB_TICKERS that wasn't already loaded by `scan_all()`.

**Step 4 — Verify:**
```bash
cd /home/user/projects/TQA-MOEX
python3 -c "
from trading_bot.cron_scanner import main
result = main()
print(result[:200])
" 2>&1 | head -20
```

---

### Task 5: Add rolling WR to dashboard

**Objective:** Dashboard shows only global WR. Add rolling WR over last N trades for early degradation detection.

**Files:**
- Modify: `/home/user/projects/TQA-MOEX/trading_bot/dashboard.py`

**Step 1 — Add `rolling_winrate` function:**
Add after `_calc_stats()`:
```python
def _rolling_winrate(trades: list[dict], window: int = 50) -> list[dict]:
    """Calculate rolling WR over sliding window. Returns list of {n, wr, pnl_cum} snapshots."""
    pnls = [float(t.get('pnl_rub', 0)) for t in trades]
    snapshots = []
    for i in range(window, len(pnls)+1):
        chunk = pnls[i-window:i]
        wins = sum(1 for p in chunk if p > 0)
        wr = round(wins / window * 100, 1)
        cum = round(sum(pnls[:i]), 0)
        snapshots.append({'n': i, 'wr': wr, 'cum_pnl': cum})
    return snapshots
```

**Step 2 — Add rolling WR section to HTML template:**
In the HTML generation, add a section:
```html
<h3>📉 Rolling WR (last 50 trades)</h3>
<div id="rolling-wr-chart">
  <svg width="800" height="200">
    <!-- Plot rolling WR as polyline -->
  </svg>
</div>
```
Show last 10 rolling WR values as a table + mini sparkline: recent WR trend. If wr has dropped below 40%, show ⚠️ warning.

**Step 3 — Add to routes:**
In the `do_GET` handler, compute rolling stats and inject into the HTML context.

**Step 4 — Verify:**
```bash
cd /home/user/projects/TQA-MOEX
timeout 5 python3 -c "
from trading_bot.dashboard import _rolling_winrate
result = _rolling_winrate([{'pnl_rub': '100'}, {'pnl_rub': '-50'}], window=2)
print(f'Rolling WR computed: {result}')
" 2>&1 | head -10
```

---

### Task 6: Optimize equity dashboard HTML size

**Objective:** `moex_equity_dashboard.py` serves a ~12MB HTML. This is because Canvas equity data is inlined. Reduce to ~2MB.

**Problem:** The dashboard embeds full equity curves as Canvas JS data arrays. For N tickers over several years, this is tens of thousands of data points per ticker.

**Step 1 — Quantize data:**
Before inlining, apply a simple decimation: keep first, last, and every Nth point where N = max(1, len(data) // 1000). This keeps visual fidelity but cuts data size by ~10x.

**Step 2 — LZ-string compress inline data:**
If the user has a browser that supports it, use `lz-string` compression in JS to compress the inline JSON. But simplest: just decimate.

**Step 3 — Replace inline Canvas drawing with a single SVG polyline:**
Instead of JS arrays + Canvas loop, generate SVG `<polyline>` elements server-side. SVG is more compact for the same data.

**Step 4 — Verify size:**
```bash
cd /home/user/projects/TQA-MOEX
python3 -c "
import moex_equity_dashboard as d
# Start server briefly and check response size
import http.server, threading, time
def check():
    time.sleep(2)
    import urllib.request
    resp = urllib.request.urlopen('http://localhost:5059/')
    data = resp.read()
    print(f'Response size: {len(data)/1024:.1f} KB')
    exit(0)
t = threading.Thread(target=check, daemon=True)
t.start()
d.run(port=5059)
" 2>&1 | head -5
```

---

## Verification Checklist

After all tasks:

1. `cd /home/user/projects/TQA-MOEX && python3 -m trading_bot.cron_scanner --healthcheck` — should return DB OK
2. `python3 -m trading_bot.cron_scanner` — should run without errors (might produce 0 signals on weekend)
3. `python3 -m trading_bot.dashboard --port 5080 &` — dashboard serves on :5080
4. `python3 moex_equity_dashboard.py &` — equity dashboard serves on :5057
5. `cd /home/user/projects/TQA-MOEX && git status` — all changes tracked, no dead files
